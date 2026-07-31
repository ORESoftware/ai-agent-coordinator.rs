use super::*;

pub(super) fn classify_message(
    source: &SourceConfig,
    message: &ConnectorMessage,
    now: DateTime<Utc>,
) -> Option<CandidateItem> {
    let subject_text = message.subject.to_ascii_lowercase();
    let snippet_text = message
        .snippet
        .as_deref()
        .unwrap_or_default()
        .to_ascii_lowercase();
    let categories = message
        .categories
        .iter()
        .map(|value| value.trim().to_ascii_lowercase())
        .collect::<HashSet<_>>();
    let explicit_deadline_urgent = message
        .explicit_deadline
        .is_some_and(|deadline| deadline <= now + ChronoDuration::hours(24));
    let security_or_account = categories.iter().any(|category| {
        matches!(
            category.as_str(),
            "security" | "account" | "billing" | "legal" | "incident"
        )
    });
    let relationship_follow_up = categories.iter().any(|category| {
        matches!(
            category.as_str(),
            "customer"
                | "partner"
                | "investor"
                | "hiring"
                | "job"
                | "application"
                | "funding"
                | "calendar"
                | "travel"
        )
    });
    let urgent_keyword = contains_any(
        &subject_text,
        &snippet_text,
        &[
            "urgent",
            "asap",
            "immediate",
            "action required",
            "today",
            "overdue",
        ],
    );
    let action_keyword = contains_any(
        &subject_text,
        &snippet_text,
        &[
            "please reply",
            "please confirm",
            "can you",
            "could you",
            "need your",
            "response requested",
            "decision needed",
            "follow up",
        ],
    );
    let must_surface_automated = security_or_account
        || explicit_deadline_urgent
        || message.importance == MessageImportance::High;
    if message.automated && !must_surface_automated {
        return None;
    }

    let bucket = if explicit_deadline_urgent
        || security_or_account
        || urgent_keyword
        || message.importance == MessageImportance::High
    {
        AttentionBucket::Urgent
    } else if message.direct_request
        || message.user_is_next_responder
        || message.explicit_deadline.is_some()
        || relationship_follow_up
        || action_keyword
    {
        AttentionBucket::NeedsReplySoon
    } else {
        return None;
    };

    let (reason, confidence) = if explicit_deadline_urgent {
        (
            "An explicit message deadline is due within 24 hours or is overdue.",
            0.99,
        )
    } else if security_or_account {
        (
            "The connector classified this as a security, account, billing, legal, or incident item requiring review.",
            0.97,
        )
    } else if message.importance == MessageImportance::High {
        (
            "The source connector marked this message as high importance.",
            0.94,
        )
    } else if urgent_keyword {
        (
            "The subject or connector-provided snippet contains time-sensitive language.",
            0.88,
        )
    } else if message.user_is_next_responder {
        (
            "Thread state indicates that you are the next expected responder.",
            0.93,
        )
    } else if message.direct_request {
        (
            "The connector detected a direct request addressed to you.",
            0.91,
        )
    } else if message.explicit_deadline.is_some() {
        ("The message contains an explicit future deadline.", 0.90)
    } else if relationship_follow_up {
        (
            "The message is an active customer, partner, hiring, application, funding, calendar, or travel follow-up.",
            0.84,
        )
    } else {
        (
            "The message contains a direct action or response cue.",
            0.76,
        )
    };

    let deadline = message.explicit_deadline.map(|at| DeadlineEvidence {
        at,
        source: "explicit",
    });
    let reference = stable_reference(&source.id, &message.stable_id);
    let item = AttentionNotificationItem {
        mailbox: source.id.clone(),
        provider: source.provider.as_str().to_owned(),
        reference,
        sender: normalize_display_text(&message.sender),
        subject: normalize_display_text(&message.subject),
        received_at: message.received_at,
        reason: reason.to_owned(),
        confidence,
        deadline,
        recommended_next_action: recommended_next_action(bucket, security_or_account).to_owned(),
    };
    let fingerprint = message_fingerprint(source, message, bucket, reason);

    Some(CandidateItem {
        source_id: source.id.clone(),
        stable_id: message.stable_id.clone(),
        fingerprint,
        bucket,
        item,
    })
}

fn recommended_next_action(bucket: AttentionBucket, security_or_account: bool) -> &'static str {
    if security_or_account {
        "Open the original message through the connected mailbox and verify the account, security, billing, legal, or incident request before acting."
    } else if bucket == AttentionBucket::Urgent {
        "Open the original thread, verify the stated deadline, and respond or delegate as soon as practical."
    } else {
        "Open the original thread, confirm that you are the next responder, and prepare a reply or explicit follow-up."
    }
}

pub(super) fn should_emit(
    candidate: &CandidateItem,
    state: Option<&ItemState>,
    now: DateTime<Utc>,
    reminder_interval: ChronoDuration,
) -> bool {
    let Some(state) = state else {
        return true;
    };
    if state.pending_delivery_key.is_some() {
        return false;
    }
    if state.last_emitted_fingerprint.as_deref() != Some(candidate.fingerprint.as_str()) {
        return true;
    }
    candidate.bucket == AttentionBucket::Urgent
        && state
            .last_emitted_at
            .is_some_and(|last_emitted| last_emitted + reminder_interval <= now)
}

pub(super) fn validate_sources(sources: &[SourceConfig]) -> Result<()> {
    if sources.len() > 32 {
        bail!("{SOURCES_ENV} may contain at most 32 enabled sources");
    }
    let mut ids = HashSet::new();
    for source in sources {
        validate_source_id(&source.id)?;
        if !ids.insert(source.id.clone()) {
            bail!("{SOURCES_ENV} contains duplicate source id {:?}", source.id);
        }
        validate_safe_endpoint(&source.endpoint, SOURCES_ENV)?;
        if let Some(token_env) = source.token_env.as_deref() {
            validate_env_name(token_env)?;
        }
    }
    Ok(())
}

pub(super) fn validate_connector_response(
    source: &SourceConfig,
    response: &ConnectorScanResponse,
    max_messages: usize,
) -> Result<()> {
    if response.messages.len() > max_messages {
        bail!(
            "connector returned more than the configured message limit for source {}",
            source.id
        );
    }
    if response.next_cursor.as_ref().is_some_and(|cursor| {
        cursor.is_empty() || cursor.len() > 4_096 || cursor.chars().any(char::is_control)
    }) {
        bail!(
            "connector returned an invalid cursor for source {}",
            source.id
        );
    }

    let mut stable_ids = HashSet::new();
    for message in &response.messages {
        validate_bounded_field("stable_id", &message.stable_id, 1, 512)?;
        if !stable_ids.insert(message.stable_id.clone()) {
            bail!(
                "connector returned duplicate stable_id for source {}",
                source.id
            );
        }
        if let Some(thread_id) = message.thread_id.as_deref() {
            validate_bounded_field("thread_id", thread_id, 1, 512)?;
        }
        validate_bounded_field("sender", &message.sender, 1, 512)?;
        validate_bounded_field("subject", &message.subject, 1, 1_024)?;
        if message
            .snippet
            .as_ref()
            .is_some_and(|snippet| snippet.len() > 4_096 || contains_disallowed_control(snippet))
        {
            bail!(
                "connector returned an invalid snippet for source {}",
                source.id
            );
        }
        if message.categories.len() > 32
            || message.categories.iter().any(|category| {
                category.trim().is_empty()
                    || category.len() > 64
                    || contains_disallowed_control(category)
            })
        {
            bail!(
                "connector returned invalid categories for source {}",
                source.id
            );
        }
        if message.material_version.as_ref().is_some_and(|version| {
            version.trim().is_empty()
                || version.len() > 256
                || version.chars().any(char::is_control)
        }) {
            bail!(
                "connector returned invalid material_version for source {}",
                source.id
            );
        }
    }
    Ok(())
}

fn validate_source_id(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'))
    {
        bail!("email-attention source ids must use 1..=64 ASCII letters, digits, dot, dash, or underscore");
    }
    Ok(())
}

fn validate_bounded_field(name: &str, value: &str, min: usize, max: usize) -> Result<()> {
    if value.len() < min
        || value.len() > max
        || value.trim().is_empty()
        || value.chars().any(char::is_control)
    {
        bail!("connector field {name} is outside its allowed visible-text bounds");
    }
    Ok(())
}

pub(super) fn validate_safe_endpoint(value: &str, variable: &str) -> Result<()> {
    let url = Url::parse(value).with_context(|| format!("{variable} contains an invalid URL"))?;
    if !url.username().is_empty() || url.password().is_some() || url.fragment().is_some() {
        bail!("{variable} URLs must not contain credentials or fragments");
    }
    let host = url
        .host_str()
        .ok_or_else(|| anyhow!("{variable} URL must contain a host"))?;
    let loopback = matches!(host, "localhost" | "127.0.0.1" | "::1");
    if url.scheme() != "https" && !(url.scheme() == "http" && loopback) {
        bail!("{variable} must use HTTPS, except loopback HTTP is allowed for tests");
    }
    Ok(())
}

pub(super) fn optional_env_name(value: &str) -> Result<Option<String>> {
    let value = value.trim();
    if value.is_empty() {
        return Ok(None);
    }
    validate_env_name(value)?;
    Ok(Some(value.to_owned()))
}

fn validate_env_name(value: &str) -> Result<()> {
    if value.is_empty()
        || value.len() > 128
        || !value.bytes().enumerate().all(|(index, byte)| {
            byte == b'_' || byte.is_ascii_uppercase() || (index > 0 && byte.is_ascii_digit())
        })
    {
        bail!("credential environment names must use uppercase ASCII letters, digits, and underscores and cannot start with a digit");
    }
    Ok(())
}

pub(super) fn read_secret_env(name: &str) -> Result<String> {
    validate_env_name(name)?;
    env::var(name)
        .ok()
        .map(|value| value.trim().to_owned())
        .filter(|value| !value.is_empty())
        .ok_or_else(|| anyhow!("required credential environment variable {name} is not set"))
}

pub(super) fn read_bool_env(name: &str, default: bool) -> Result<bool> {
    match env::var(name) {
        Ok(value) => match value.trim().to_ascii_lowercase().as_str() {
            "1" | "true" | "yes" | "on" => Ok(true),
            "0" | "false" | "no" | "off" => Ok(false),
            _ => bail!("{name} must be a boolean"),
        },
        Err(env::VarError::NotPresent) => Ok(default),
        Err(error) => Err(error).with_context(|| format!("failed to read {name}")),
    }
}

pub(super) fn read_u32_env(name: &str, default: u32, min: u32, max: u32) -> Result<u32> {
    let value = match env::var(name) {
        Ok(value) => value
            .trim()
            .parse::<u32>()
            .with_context(|| format!("{name} must be an unsigned integer"))?,
        Err(env::VarError::NotPresent) => default,
        Err(error) => return Err(error).with_context(|| format!("failed to read {name}")),
    };
    if !(min..=max).contains(&value) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(value)
}

pub(super) fn read_u64_env(name: &str, default: u64, min: u64, max: u64) -> Result<u64> {
    let value = match env::var(name) {
        Ok(value) => value
            .trim()
            .parse::<u64>()
            .with_context(|| format!("{name} must be an unsigned integer"))?,
        Err(env::VarError::NotPresent) => default,
        Err(error) => return Err(error).with_context(|| format!("failed to read {name}")),
    };
    if !(min..=max).contains(&value) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(value)
}

pub(super) fn read_i64_env(name: &str, default: i64, min: i64, max: i64) -> Result<i64> {
    let value = match env::var(name) {
        Ok(value) => value
            .trim()
            .parse::<i64>()
            .with_context(|| format!("{name} must be an integer"))?,
        Err(env::VarError::NotPresent) => default,
        Err(error) => return Err(error).with_context(|| format!("failed to read {name}")),
    };
    if !(min..=max).contains(&value) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(value)
}

pub(super) fn read_usize_env(name: &str, default: usize, min: usize, max: usize) -> Result<usize> {
    let value = match env::var(name) {
        Ok(value) => value
            .trim()
            .parse::<usize>()
            .with_context(|| format!("{name} must be an unsigned integer"))?,
        Err(env::VarError::NotPresent) => default,
        Err(error) => return Err(error).with_context(|| format!("failed to read {name}")),
    };
    if !(min..=max).contains(&value) {
        bail!("{name} must be between {min} and {max}");
    }
    Ok(value)
}

pub(super) async fn read_bounded_response(
    mut response: reqwest::Response,
    max_bytes: usize,
) -> Result<Vec<u8>> {
    let mut body = Vec::new();
    while let Some(chunk) = response
        .chunk()
        .await
        .context("failed to read upstream response chunk")?
    {
        if body.len().saturating_add(chunk.len()) > max_bytes {
            bail!("upstream response exceeds configured byte limit");
        }
        body.extend_from_slice(&chunk);
    }
    Ok(body)
}

fn contains_disallowed_control(value: &str) -> bool {
    value
        .chars()
        .any(|character| character.is_control() && !matches!(character, '\n' | '\r' | '\t'))
}

pub(super) fn reject_oversized_content_length(
    response: &reqwest::Response,
    max_bytes: usize,
) -> Result<()> {
    if response
        .headers()
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<usize>().ok())
        .is_some_and(|length| length > max_bytes)
    {
        bail!("upstream response exceeds configured byte limit");
    }
    Ok(())
}

fn message_fingerprint(
    source: &SourceConfig,
    message: &ConnectorMessage,
    bucket: AttentionBucket,
    reason: &str,
) -> String {
    let mut hasher = Sha256::new();
    let normalized_sender = normalize_display_text(&message.sender);
    let normalized_subject = normalize_display_text(&message.subject);
    let normalized_snippet = normalize_display_text(message.snippet.as_deref().unwrap_or_default());
    for value in [
        source.id.as_str(),
        source.provider.as_str(),
        message.stable_id.as_str(),
        message.thread_id.as_deref().unwrap_or_default(),
        normalized_sender.as_str(),
        normalized_subject.as_str(),
        normalized_snippet.as_str(),
        message.material_version.as_deref().unwrap_or_default(),
        bucket.as_str(),
        reason,
    ] {
        hasher.update(value.as_bytes());
        hasher.update([0]);
    }
    hasher.update(message.received_at.timestamp_millis().to_be_bytes());
    hasher.update(
        message
            .explicit_deadline
            .map(|value| value.timestamp_millis())
            .unwrap_or_default()
            .to_be_bytes(),
    );
    hasher.update([
        message.direct_request as u8,
        message.user_is_next_responder as u8,
        message.automated as u8,
        match message.importance {
            MessageImportance::Low => 0,
            MessageImportance::Normal => 1,
            MessageImportance::High => 2,
        },
    ]);
    let mut categories = message
        .categories
        .iter()
        .map(|category| category.trim().to_ascii_lowercase())
        .collect::<Vec<_>>();
    categories.sort();
    categories.dedup();
    for category in categories {
        hasher.update(category.as_bytes());
        hasher.update([0]);
    }
    hex::encode(hasher.finalize())
}

pub(super) fn delivery_idempotency_key(run_id: &str, candidates: &[CandidateItem]) -> String {
    let mut identities = candidates
        .iter()
        .map(|candidate| {
            format!(
                "{}\0{}\0{}",
                candidate.source_id, candidate.stable_id, candidate.fingerprint
            )
        })
        .collect::<Vec<_>>();
    identities.sort();
    let mut hasher = Sha256::new();
    hasher.update(run_id.as_bytes());
    hasher.update([0]);
    for identity in identities {
        hasher.update(identity.as_bytes());
        hasher.update([0]);
    }
    format!("email-attention:{}", hex::encode(hasher.finalize()))
}

fn stable_reference(source_id: &str, stable_id: &str) -> String {
    let mut hasher = Sha256::new();
    hasher.update(source_id.as_bytes());
    hasher.update([0]);
    hasher.update(stable_id.as_bytes());
    let digest = hex::encode(hasher.finalize());
    digest[..16].to_owned()
}

fn contains_any(subject: &str, snippet: &str, keywords: &[&str]) -> bool {
    keywords
        .iter()
        .any(|keyword| subject.contains(keyword) || snippet.contains(keyword))
}

fn normalize_display_text(value: &str) -> String {
    value.split_whitespace().collect::<Vec<_>>().join(" ")
}

pub(super) fn bounded_text(value: &str, max_chars: usize) -> String {
    value.chars().take(max_chars).collect()
}

pub(super) fn default_true() -> bool {
    true
}
