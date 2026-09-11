---- MODULE ContinuousRecoverySupervisor ----
EXTENDS Naturals, FiniteSets

(*
Peer specification for the production recovery supervisor.

The executable bounded checker is formal/continuous_recovery_supervisor.py.
This TLA+ module is intentionally kept dependency-free in source review; CI
checks its required actions and invariants but does not claim an unbounded TLC
proof.
*)

CONSTANTS Workers, MaxRestarts, MaxBackoff

ASSUME /\ Workers \subseteq Nat
       /\ Cardinality(Workers) \in 1..3
       /\ MaxRestarts \in Nat
       /\ MaxBackoff \in Nat \ {0}

Phases == {"running", "backoff", "stopping", "killing", "stopped", "stuck"}
LivePhases == {"running", "stopping", "killing", "stuck"}

VARIABLES phase,
          attempts,
          backoff,
          generation,
          stopping,
          operatorStop,
          budgetExhausted,
          terminateGraceElapsed,
          killGraceElapsed,
          killTimeout

vars == <<phase, attempts, backoff, generation, stopping, operatorStop,
          budgetExhausted, terminateGraceElapsed, killGraceElapsed,
          killTimeout>>

BackoffFor(n) ==
    IF 2^(n - 1) <= MaxBackoff THEN 2^(n - 1) ELSE MaxBackoff

StopPhase(value) ==
    CASE value = "running" -> "stopping"
      [] value = "backoff" -> "stopped"
      [] OTHER -> value

Init ==
    /\ phase = [i \in Workers |-> "running"]
    /\ attempts = [i \in Workers |-> 0]
    /\ backoff = [i \in Workers |-> 0]
    /\ generation = [i \in Workers |-> 0]
    /\ stopping = FALSE
    /\ operatorStop = FALSE
    /\ budgetExhausted = FALSE
    /\ terminateGraceElapsed = FALSE
    /\ killGraceElapsed = FALSE
    /\ killTimeout = FALSE

Crash(i) ==
    /\ ~stopping
    /\ phase[i] = "running"
    /\ LET nextAttempt == attempts[i] + 1 IN
       IF nextAttempt > MaxRestarts
       THEN
         /\ phase' =
              [j \in Workers |->
                 IF j = i THEN "stopped" ELSE StopPhase(phase[j])]
         /\ attempts' = [attempts EXCEPT ![i] = nextAttempt]
         /\ backoff' = [j \in Workers |-> 0]
         /\ stopping' = TRUE
         /\ budgetExhausted' = TRUE
       ELSE
         /\ phase' = [phase EXCEPT ![i] = "backoff"]
         /\ attempts' = [attempts EXCEPT ![i] = nextAttempt]
         /\ backoff' = [backoff EXCEPT ![i] = BackoffFor(nextAttempt)]
         /\ stopping' = stopping
         /\ budgetExhausted' = budgetExhausted
    /\ UNCHANGED <<generation, operatorStop, terminateGraceElapsed,
                   killGraceElapsed, killTimeout>>

StableExit(i) ==
    /\ ~stopping
    /\ phase[i] = "running"
    /\ phase' = [phase EXCEPT ![i] = "backoff"]
    /\ attempts' = [attempts EXCEPT ![i] = 1]
    /\ backoff' = [backoff EXCEPT ![i] = BackoffFor(1)]
    /\ UNCHANGED <<generation, stopping, operatorStop, budgetExhausted,
                   terminateGraceElapsed, killGraceElapsed, killTimeout>>

BackoffElapsed(i) ==
    /\ ~stopping
    /\ phase[i] = "backoff"
    /\ phase' = [phase EXCEPT ![i] = "running"]
    /\ backoff' = [backoff EXCEPT ![i] = 0]
    /\ generation' = [generation EXCEPT ![i] = @ + 1]
    /\ UNCHANGED <<attempts, stopping, operatorStop, budgetExhausted,
                   terminateGraceElapsed, killGraceElapsed, killTimeout>>

WindowElapsed(i) ==
    /\ ~stopping
    /\ phase[i] = "running"
    /\ attempts[i] > 0
    /\ attempts' = [attempts EXCEPT ![i] = 0]
    /\ UNCHANGED <<phase, backoff, generation, stopping, operatorStop,
                   budgetExhausted, terminateGraceElapsed,
                   killGraceElapsed, killTimeout>>

StopRequested ==
    /\ ~stopping
    /\ phase' = [i \in Workers |-> StopPhase(phase[i])]
    /\ backoff' = [i \in Workers |-> 0]
    /\ stopping' = TRUE
    /\ operatorStop' = TRUE
    /\ UNCHANGED <<attempts, generation, budgetExhausted,
                   terminateGraceElapsed, killGraceElapsed, killTimeout>>

ChildExited(i) ==
    /\ stopping
    /\ phase[i] \in {"stopping", "killing"}
    /\ phase' = [phase EXCEPT ![i] = "stopped"]
    /\ backoff' = [backoff EXCEPT ![i] = 0]
    /\ UNCHANGED <<attempts, generation, stopping, operatorStop,
                   budgetExhausted, terminateGraceElapsed,
                   killGraceElapsed, killTimeout>>

TerminateGraceExpired ==
    /\ stopping
    /\ ~terminateGraceElapsed
    /\ \E i \in Workers : phase[i] = "stopping"
    /\ phase' =
         [i \in Workers |->
            IF phase[i] = "stopping" THEN "killing" ELSE phase[i]]
    /\ terminateGraceElapsed' = TRUE
    /\ UNCHANGED <<attempts, backoff, generation, stopping, operatorStop,
                   budgetExhausted, killGraceElapsed, killTimeout>>

KillGraceExpired ==
    /\ stopping
    /\ terminateGraceElapsed
    /\ ~killGraceElapsed
    /\ \E i \in Workers : phase[i] = "killing"
    /\ phase' =
         [i \in Workers |->
            IF phase[i] = "killing" THEN "stuck" ELSE phase[i]]
    /\ killGraceElapsed' = TRUE
    /\ killTimeout' = TRUE
    /\ UNCHANGED <<attempts, backoff, generation, stopping, operatorStop,
                   budgetExhausted, terminateGraceElapsed>>

Next ==
    \/ \E i \in Workers : Crash(i)
    \/ \E i \in Workers : StableExit(i)
    \/ \E i \in Workers : BackoffElapsed(i)
    \/ \E i \in Workers : WindowElapsed(i)
    \/ StopRequested
    \/ \E i \in Workers : ChildExited(i)
    \/ TerminateGraceExpired
    \/ KillGraceExpired

TypeOK ==
    /\ phase \in [Workers -> Phases]
    /\ attempts \in [Workers -> Nat]
    /\ backoff \in [Workers -> Nat]
    /\ generation \in [Workers -> Nat]
    /\ stopping \in BOOLEAN
    /\ operatorStop \in BOOLEAN
    /\ budgetExhausted \in BOOLEAN
    /\ terminateGraceElapsed \in BOOLEAN
    /\ killGraceElapsed \in BOOLEAN
    /\ killTimeout \in BOOLEAN

AtMostThreeLiveChildren ==
    Cardinality({i \in Workers : phase[i] \in LivePhases}) <= 3

NoRespawnAfterStop ==
    stopping => \A i \in Workers : phase[i] # "backoff"

GraceOrder ==
    /\ killGraceElapsed => terminateGraceElapsed
    /\ killTimeout => killGraceElapsed
    /\ terminateGraceElapsed => stopping

BackoffExact ==
    \A i \in Workers :
      IF phase[i] = "backoff"
      THEN /\ attempts[i] \in 1..MaxRestarts
           /\ backoff[i] = BackoffFor(attempts[i])
      ELSE backoff[i] = 0

AttemptBound ==
    \A i \in Workers : attempts[i] <= MaxRestarts + 1

Safety ==
    /\ TypeOK
    /\ AtMostThreeLiveChildren
    /\ NoRespawnAfterStop
    /\ GraceOrder
    /\ BackoffExact
    /\ AttemptBound

Spec == Init /\ [][Next]_vars

THEOREM Spec => []Safety
====
