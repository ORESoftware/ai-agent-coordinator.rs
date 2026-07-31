use anyhow::{bail, Context, Result};
use chrono::{
    DateTime, Datelike, Duration as ChronoDuration, NaiveDate, NaiveDateTime, Utc, Weekday,
};

const NEW_YORK_STANDARD_OFFSET_SECONDS: i32 = -5 * 60 * 60;
const NEW_YORK_DAYLIGHT_OFFSET_SECONDS: i32 = -4 * 60 * 60;

#[derive(Debug, Clone)]
pub(super) struct Schedule {
    timezone: Timezone,
    weekdays: [bool; 7],
    local_hour: u32,
    local_minute: u32,
}

#[derive(Debug, Clone)]
enum Timezone {
    Utc,
    AmericaNewYork,
    FixedOffset { name: String, seconds: i32 },
}

impl Schedule {
    pub(super) fn parse(
        timezone: &str,
        weekdays: &str,
        local_hour: u32,
        local_minute: u32,
    ) -> Result<Self> {
        if local_hour > 23 {
            bail!("EMAIL_ATTENTION_LOCAL_HOUR must be between 0 and 23");
        }
        if local_minute > 59 {
            bail!("EMAIL_ATTENTION_LOCAL_MINUTE must be between 0 and 59");
        }

        Ok(Self {
            timezone: Timezone::parse(timezone)?,
            weekdays: parse_weekdays(weekdays)?,
            local_hour,
            local_minute,
        })
    }

    pub(super) fn timezone_name(&self) -> &str {
        self.timezone.name()
    }

    pub(super) fn weekday_names(&self) -> Vec<&'static str> {
        WEEKDAYS
            .iter()
            .enumerate()
            .filter_map(|(index, (_, name))| self.weekdays[index].then_some(*name))
            .collect()
    }

    pub(super) fn local_hour(&self) -> u32 {
        self.local_hour
    }

    pub(super) fn local_minute(&self) -> u32 {
        self.local_minute
    }

    pub(super) fn next_after(&self, now: DateTime<Utc>) -> DateTime<Utc> {
        let current_offset = self.timezone.offset_for_utc(now);
        let local_now = now.naive_utc() + ChronoDuration::seconds(i64::from(current_offset));
        let first_date = local_now.date();

        for day_offset in 0..=14 {
            let date = first_date
                .checked_add_signed(ChronoDuration::days(day_offset))
                .expect("two-week schedule search must remain in range");
            if !self.weekdays[weekday_index(date.weekday())] {
                continue;
            }

            let local_candidate = date
                .and_hms_opt(self.local_hour, self.local_minute, 0)
                .expect("validated schedule time must construct");
            let local_candidate = self.timezone.normalize_local_candidate(local_candidate);
            let offset = self.timezone.offset_for_local(local_candidate);
            let utc_candidate = DateTime::<Utc>::from_naive_utc_and_offset(
                local_candidate - ChronoDuration::seconds(i64::from(offset)),
                Utc,
            );
            if utc_candidate > now {
                return utc_candidate;
            }
        }

        unreachable!("a non-empty weekly schedule must have a next run within fourteen days")
    }
}

impl Timezone {
    fn parse(value: &str) -> Result<Self> {
        let value = value.trim();
        match value {
            "UTC" | "Etc/UTC" | "Z" => Ok(Self::Utc),
            "America/New_York" => Ok(Self::AmericaNewYork),
            _ => {
                let seconds = parse_fixed_offset(value).with_context(|| {
                    format!(
                        "EMAIL_ATTENTION_TIMEZONE must be America/New_York, UTC, or a fixed offset like -05:00; got {value:?}"
                    )
                })?;
                Ok(Self::FixedOffset {
                    name: value.to_owned(),
                    seconds,
                })
            }
        }
    }

    fn name(&self) -> &str {
        match self {
            Self::Utc => "UTC",
            Self::AmericaNewYork => "America/New_York",
            Self::FixedOffset { name, .. } => name,
        }
    }

    fn normalize_local_candidate(&self, local: NaiveDateTime) -> NaiveDateTime {
        match self {
            Self::AmericaNewYork => normalize_new_york_local_candidate(local),
            Self::Utc | Self::FixedOffset { .. } => local,
        }
    }

    fn offset_for_local(&self, local: NaiveDateTime) -> i32 {
        match self {
            Self::Utc => 0,
            Self::AmericaNewYork => new_york_offset_for_local(local),
            Self::FixedOffset { seconds, .. } => *seconds,
        }
    }

    fn offset_for_utc(&self, utc: DateTime<Utc>) -> i32 {
        match self {
            Self::Utc => 0,
            Self::AmericaNewYork => new_york_offset_for_utc(utc),
            Self::FixedOffset { seconds, .. } => *seconds,
        }
    }
}

const WEEKDAYS: [(Weekday, &str); 7] = [
    (Weekday::Mon, "mon"),
    (Weekday::Tue, "tue"),
    (Weekday::Wed, "wed"),
    (Weekday::Thu, "thu"),
    (Weekday::Fri, "fri"),
    (Weekday::Sat, "sat"),
    (Weekday::Sun, "sun"),
];

fn parse_weekdays(value: &str) -> Result<[bool; 7]> {
    let value = value.trim();
    if value.eq_ignore_ascii_case("daily") || value == "*" {
        return Ok([true; 7]);
    }

    let mut selected = [false; 7];
    for raw in value.split(',') {
        let day = raw.trim().to_ascii_lowercase();
        if day.is_empty() {
            continue;
        }
        let Some(index) = WEEKDAYS.iter().position(|(_, name)| *name == day) else {
            bail!(
                "EMAIL_ATTENTION_WEEKDAYS contains unsupported day {day:?}; use comma-separated mon,tue,wed,thu,fri,sat,sun or daily"
            );
        };
        selected[index] = true;
    }

    if !selected.iter().any(|value| *value) {
        bail!("EMAIL_ATTENTION_WEEKDAYS must select at least one day");
    }
    Ok(selected)
}

fn weekday_index(day: Weekday) -> usize {
    day.num_days_from_monday() as usize
}

fn parse_fixed_offset(value: &str) -> Result<i32> {
    let bytes = value.as_bytes();
    if bytes.len() != 6
        || !matches!(bytes[0], b'+' | b'-')
        || bytes[3] != b':'
        || !bytes[1..3].iter().all(u8::is_ascii_digit)
        || !bytes[4..6].iter().all(u8::is_ascii_digit)
    {
        bail!("invalid fixed offset");
    }
    let hours = i32::from((bytes[1] - b'0') * 10 + (bytes[2] - b'0'));
    let minutes = i32::from((bytes[4] - b'0') * 10 + (bytes[5] - b'0'));
    if hours > 14 || minutes > 59 || (hours == 14 && minutes != 0) {
        bail!("fixed offset is outside the supported UTC-14:00..UTC+14:00 range");
    }
    let sign = if bytes[0] == b'-' { -1 } else { 1 };
    Ok(sign * (hours * 60 * 60 + minutes * 60))
}

fn normalize_new_york_local_candidate(local: NaiveDateTime) -> NaiveDateTime {
    let (start_local, _) = new_york_transitions(local.year());
    if local >= start_local && local < start_local + ChronoDuration::hours(1) {
        local + ChronoDuration::hours(1)
    } else {
        local
    }
}

fn new_york_offset_for_local(local: NaiveDateTime) -> i32 {
    let (start_local, end_local) = new_york_transitions(local.year());
    if local >= start_local && local < end_local {
        NEW_YORK_DAYLIGHT_OFFSET_SECONDS
    } else {
        NEW_YORK_STANDARD_OFFSET_SECONDS
    }
}

fn new_york_offset_for_utc(utc: DateTime<Utc>) -> i32 {
    let (start_local, end_local) = new_york_transitions(utc.year());
    let start_utc = DateTime::<Utc>::from_naive_utc_and_offset(
        start_local - ChronoDuration::seconds(i64::from(NEW_YORK_STANDARD_OFFSET_SECONDS)),
        Utc,
    );
    let end_utc = DateTime::<Utc>::from_naive_utc_and_offset(
        end_local - ChronoDuration::seconds(i64::from(NEW_YORK_DAYLIGHT_OFFSET_SECONDS)),
        Utc,
    );
    if utc >= start_utc && utc < end_utc {
        NEW_YORK_DAYLIGHT_OFFSET_SECONDS
    } else {
        NEW_YORK_STANDARD_OFFSET_SECONDS
    }
}

fn new_york_transitions(year: i32) -> (NaiveDateTime, NaiveDateTime) {
    let start = nth_weekday_of_month(year, 3, Weekday::Sun, 2)
        .and_hms_opt(2, 0, 0)
        .expect("DST start time must construct");
    let end = nth_weekday_of_month(year, 11, Weekday::Sun, 1)
        .and_hms_opt(2, 0, 0)
        .expect("DST end time must construct");
    (start, end)
}

fn nth_weekday_of_month(year: i32, month: u32, weekday: Weekday, nth: u32) -> NaiveDate {
    let first = NaiveDate::from_ymd_opt(year, month, 1).expect("valid month start");
    let days_until = (7 + weekday.num_days_from_monday() as i64
        - first.weekday().num_days_from_monday() as i64)
        % 7;
    first
        .checked_add_signed(ChronoDuration::days(days_until + i64::from((nth - 1) * 7)))
        .expect("weekday occurrence must remain in month")
}

#[cfg(test)]
mod tests {
    use chrono::{TimeZone, Utc};

    use super::Schedule;

    #[test]
    fn new_york_schedule_tracks_daylight_saving_time() {
        let schedule =
            Schedule::parse("America/New_York", "mon,tue,wed,thu,fri", 9, 0).expect("schedule");

        let winter = Utc
            .with_ymd_and_hms(2026, 1, 5, 13, 30, 0)
            .single()
            .expect("winter timestamp");
        assert_eq!(
            schedule.next_after(winter),
            Utc.with_ymd_and_hms(2026, 1, 5, 14, 0, 0)
                .single()
                .expect("winter run")
        );

        let summer = Utc
            .with_ymd_and_hms(2026, 7, 6, 12, 30, 0)
            .single()
            .expect("summer timestamp");
        assert_eq!(
            schedule.next_after(summer),
            Utc.with_ymd_and_hms(2026, 7, 6, 13, 0, 0)
                .single()
                .expect("summer run")
        );
    }

    #[test]
    fn schedule_skips_unselected_weekends() {
        let schedule = Schedule::parse("UTC", "mon,tue,wed,thu,fri", 9, 0).expect("schedule");
        let friday_after_run = Utc
            .with_ymd_and_hms(2026, 7, 31, 10, 0, 0)
            .single()
            .expect("timestamp");
        assert_eq!(
            schedule.next_after(friday_after_run),
            Utc.with_ymd_and_hms(2026, 8, 3, 9, 0, 0)
                .single()
                .expect("monday run")
        );
    }

    #[test]
    fn fixed_offsets_are_supported_without_a_timezone_database() {
        let schedule = Schedule::parse("+05:30", "daily", 9, 15).expect("schedule");
        let now = Utc
            .with_ymd_and_hms(2026, 7, 30, 3, 0, 0)
            .single()
            .expect("timestamp");
        assert_eq!(
            schedule.next_after(now),
            Utc.with_ymd_and_hms(2026, 7, 30, 3, 45, 0)
                .single()
                .expect("scheduled run")
        );
    }

    #[test]
    fn nonexistent_new_york_time_moves_forward_into_daylight_time() {
        let schedule = Schedule::parse("America/New_York", "sun", 2, 30).expect("schedule");
        let before_transition = Utc
            .with_ymd_and_hms(2026, 3, 8, 6, 0, 0)
            .single()
            .expect("timestamp");
        assert_eq!(
            schedule.next_after(before_transition),
            Utc.with_ymd_and_hms(2026, 3, 8, 7, 30, 0)
                .single()
                .expect("normalized run")
        );
    }

    #[test]
    fn non_ascii_fixed_offset_fails_without_panicking() {
        assert!(Schedule::parse("+０5:00", "daily", 9, 0).is_err());
    }

    #[test]
    fn invalid_timezone_fails_closed() {
        let error = Schedule::parse("America/Unknown", "mon", 9, 0)
            .expect_err("unsupported timezone must fail");
        assert!(error.to_string().contains("EMAIL_ATTENTION_TIMEZONE"));
    }
}
