# core/pii_scrubber.py
# ─────────────────────────────────────────────────────────────────────────────
# METACOGNITIVE WFM ENGINE — PII Scrubber & Anonymizer
# Responsibilities:
#   1. Detect and replace Personally Identifiable Information in raw agent logs
#   2. Replace agent_id fields with context-aware person_N placeholders
#   3. Scrub PII patterns from free-text log bodies:
#        - Full names, email addresses, phone numbers
#        - National ID / SSN patterns
#        - Credit card numbers
#        - IP addresses
#        - Date-of-birth patterns
#   4. Maintain a session-scoped identity map for consistent placeholder reuse
#   5. Guarantee RawAgentLog never escapes this module
# ─────────────────────────────────────────────────────────────────────────────

from __future__ import annotations

import hashlib
import logging
import re
import threading
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from core.models import RawAgentLog, ScrubbedAgentLog

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1: PII REGEX PATTERNS
# Ordered by specificity — more specific patterns applied first.
# Each tuple: (pattern_name, compiled_regex, replacement_template)
# ─────────────────────────────────────────────────────────────────────────────

_PII_PATTERNS: List[Tuple[str, re.Pattern, str]] = [

    # ── Credit card numbers (16-digit with optional dashes/spaces) ─────────
    (
        "CREDIT_CARD",
        re.compile(
            r"\b(?:\d[ -]?){13,15}\d\b",
            re.IGNORECASE,
        ),
        "[CREDIT_CARD_REDACTED]",
    ),

    # ── US Social Security Numbers (SSN) ──────────────────────────────────
    (
        "SSN",
        re.compile(
            r"\b\d{3}[-\s]?\d{2}[-\s]?\d{4}\b",
        ),
        "[SSN_REDACTED]",
    ),

    # ── Email addresses ───────────────────────────────────────────────────
    (
        "EMAIL",
        re.compile(
            r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        ),
        "[EMAIL_REDACTED]",
    ),

    # ── International phone numbers ───────────────────────────────────────
    (
        "PHONE",
        re.compile(
            r"(?<!\d)"
            r"(\+?1?\s?)?(\(?\d{3}\)?[\s.\-]?)(\d{3}[\s.\-]?\d{4})"
            r"(?!\d)",
        ),
        "[PHONE_REDACTED]",
    ),

    # ── IPv4 addresses ────────────────────────────────────────────────────
    (
        "IPV4",
        re.compile(
            r"\b(?:\d{1,3}\.){3}\d{1,3}\b",
        ),
        "[IP_REDACTED]",
    ),

    # ── IPv6 addresses ────────────────────────────────────────────────────
    (
        "IPV6",
        re.compile(
            r"\b(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}\b"
            r"|\b(?:[0-9a-fA-F]{1,4}:)*:(?:[0-9a-fA-F]{1,4}:)*"
            r"[0-9a-fA-F]{1,4}\b",
        ),
        "[IP_REDACTED]",
    ),

    # ── Dates of birth (common formats: MM/DD/YYYY, DD-MM-YYYY, YYYY.MM.DD)
    (
        "DATE_OF_BIRTH",
        re.compile(
            r"\b(?:\d{1,2}[\/\-\.]\d{1,2}[\/\-\.]\d{2,4})\b",
        ),
        "[DATE_REDACTED]",
    ),

    # ── Passport-style ID numbers (letter + 7–9 digits) ───────────────────
    (
        "PASSPORT_ID",
        re.compile(
            r"\b[A-Z]{1,2}\d{7,9}\b",
        ),
        "[ID_REDACTED]",
    ),

    # ── Egyptian National ID (14-digit starting with 2 or 3) ─────────────
    (
        "NATIONAL_ID_EG",
        re.compile(
            r"\b[23]\d{13}\b",
        ),
        "[NATIONAL_ID_REDACTED]",
    ),

    # ── Full names: Title + Capitalized words (heuristic) ─────────────────
    # Matches: "Mr. John Smith", "Dr. Sarah Al-Farsi", "Ms. Layla Hassan"
    (
        "FULL_NAME_TITLED",
        re.compile(
            r"\b(?:Mr\.?|Mrs\.?|Ms\.?|Dr\.?|Prof\.?)\s"
            r"[A-Z][a-z]+(?:\s[A-Z][a-z\-]+){1,3}\b",
        ),
        "[NAME_REDACTED]",
    ),

    # ── Standalone capitalized name pairs (two consecutive proper nouns) ──
    # Matches: "John Smith", "Layla Hassan" — lower false positive risk
    (
        "FULL_NAME_PAIR",
        re.compile(
            r"\b[A-Z][a-z]{2,}\s[A-Z][a-z]{2,}\b",
        ),
        "[NAME_REDACTED]",
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2: SESSION IDENTITY MAP
# Maps raw agent_id → person_N placeholder.
# Thread-safe; persists for the lifetime of one ingestor session.
# Reset between batch jobs to avoid cross-session leakage.
# ─────────────────────────────────────────────────────────────────────────────

class IdentityMap:
    """
    Thread-safe mapping from raw agent identifiers to anonymized placeholders.

    Uses deterministic hashing so the same agent_id always maps to the same
    person_N within a session, enabling log correlation without re-identification.
    """

    def __init__(self):
        self._lock    : threading.Lock      = threading.Lock()
        self._map     : Dict[str, str]      = {}
        self._counter : int                 = 0

    def get_or_create(self, raw_id: str) -> str:
        """
        Return the existing placeholder for raw_id, or create a new one.

        Args:
            raw_id: Raw agent identifier (e.g., employee number, login name).

        Returns:
            Placeholder string: "person_1", "person_2", etc.
        """
        with self._lock:
            if raw_id not in self._map:
                self._counter += 1
                self._map[raw_id] = f"person_{self._counter}"
                logger.debug(
                    "IdentityMap: '%s' → '%s'",
                    self._anonymize_for_log(raw_id),
                    self._map[raw_id],
                )
            return self._map[raw_id]

    def reset(self) -> None:
        """Clear all mappings. Call between batch sessions."""
        with self._lock:
            self._map.clear()
            self._counter = 0
        logger.info("IdentityMap reset.")

    def size(self) -> int:
        """Return number of unique identities mapped."""
        with self._lock:
            return len(self._map)

    @staticmethod
    def _anonymize_for_log(raw_id: str) -> str:
        """
        Produce a safe log-safe representation of a raw ID
        (first 2 chars + SHA-256 prefix) — never logs full PII.
        """
        digest = hashlib.sha256(raw_id.encode()).hexdigest()[:6]
        prefix = raw_id[:2] if len(raw_id) >= 2 else raw_id
        return f"{prefix}***[{digest}]"


# Module-level shared identity map — reset by Orchestrator between jobs
_IDENTITY_MAP = IdentityMap()


def reset_identity_map() -> None:
    """Public reset hook for Orchestrator to call between batch sessions."""
    _IDENTITY_MAP.reset()


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3: TEXT SCRUBBER
# Applies all regex patterns to a free-text string.
# Returns scrubbed text and a count of replacements made.
# ─────────────────────────────────────────────────────────────────────────────

def scrub_text(raw_text: str) -> Tuple[str, int]:
    """
    Apply all PII regex patterns to raw_text in order of specificity.

    Args:
        raw_text: Unsanitized free-text string from an agent log.

    Returns:
        Tuple of (scrubbed_text: str, total_replacements: int).
    """
    scrubbed      = raw_text
    total_replaced = 0

    for pattern_name, pattern, replacement in _PII_PATTERNS:
        scrubbed, count = pattern.subn(replacement, scrubbed)
        if count > 0:
            logger.debug(
                "PII pattern '%s' matched %d time(s).",
                pattern_name, count,
            )
            total_replaced += count

    return scrubbed, total_replaced


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4: SINGLE LOG SCRUBBER
# Processes one RawAgentLog → ScrubbedAgentLog.
# This is the only function that touches RawAgentLog.
# ─────────────────────────────────────────────────────────────────────────────

def scrub_agent_log(
    raw_log: RawAgentLog,
    identity_map: Optional[IdentityMap] = None,
) -> ScrubbedAgentLog:
    """
    Scrub a single RawAgentLog entry.

    Steps:
        1. Map raw agent_id → person_N via IdentityMap.
        2. Replace agent_id occurrences in raw_text with placeholder.
        3. Apply all regex PII patterns to raw_text.
        4. Return ScrubbedAgentLog — safe for downstream consumption.

    Args:
        raw_log:      A RawAgentLog (PII present).
        identity_map: Optional custom IdentityMap; defaults to module-level map.

    Returns:
        ScrubbedAgentLog with all PII replaced.
    """
    imap        = identity_map or _IDENTITY_MAP
    placeholder = imap.get_or_create(raw_log.agent_id)

    # Step 1: Replace agent_id literal occurrences in text
    text_after_id_scrub = raw_log.raw_text.replace(
        raw_log.agent_id, placeholder
    )

    # Step 2: Apply regex patterns
    scrubbed_text, replacements = scrub_text(text_after_id_scrub)

    logger.debug(
        "Log [%s]: agent_id→%s | %d PII replacements made.",
        raw_log.log_id[:8], placeholder, replacements,
    )

    return ScrubbedAgentLog(
        log_id=raw_log.log_id,
        scrubbed_text=scrubbed_text,
        agent_placeholder=placeholder,
        timestamp=raw_log.timestamp,
    )


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5: BATCH SCRUBBER
# Public interface consumed by the Ingestor.
# Processes a full list of RawAgentLogs in one call.
# ─────────────────────────────────────────────────────────────────────────────

def scrub_agent_logs(
    raw_logs: List[RawAgentLog],
    identity_map: Optional[IdentityMap] = None,
) -> List[ScrubbedAgentLog]:
    """
    Scrub a list of RawAgentLog entries.

    This is the primary public interface for the PII Scrubber module.
    The Ingestor passes raw_feed.raw_agent_logs directly to this function.

    Args:
        raw_logs:     List of RawAgentLog objects (pre-scrub, PII present).
        identity_map: Optional custom IdentityMap for isolated sessions.

    Returns:
        List of ScrubbedAgentLog objects — all PII removed.
    """
    if not raw_logs:
        return []

    imap    = identity_map or _IDENTITY_MAP
    results : List[ScrubbedAgentLog] = []
    errors  : int                    = 0

    for raw_log in raw_logs:
        try:
            scrubbed = scrub_agent_log(raw_log, imap)
            results.append(scrubbed)
        except Exception as e:
            logger.error(
                "Failed to scrub log [%s]: %s",
                raw_log.log_id[:8], e,
            )
            errors += 1
            # Emit a safe fallback — never propagate raw PII on error
            results.append(
                ScrubbedAgentLog(
                    log_id=raw_log.log_id,
                    scrubbed_text="[LOG_SCRUB_ERROR — content suppressed]",
                    agent_placeholder=imap.get_or_create(raw_log.agent_id),
                    timestamp=raw_log.timestamp,
                )
            )

    logger.info(
        "Batch scrub complete: %d logs processed | %d errors.",
        len(results), errors,
    )
    return results


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6: SCRUB VALIDATOR
# Used by tests and the Orchestrator to assert no PII leaked post-scrub.
# ─────────────────────────────────────────────────────────────────────────────

def validate_scrubbed_log(scrubbed_log: ScrubbedAgentLog) -> List[str]:
    """
    Run all PII patterns against a scrubbed log to detect any leakage.

    Returns:
        List of pattern names that still match (empty = clean).
        An empty list means the log passed validation.
    """
    leaks: List[str] = []
    for pattern_name, pattern, _ in _PII_PATTERNS:
        if pattern.search(scrubbed_log.scrubbed_text):
            leaks.append(pattern_name)
            logger.warning(
                "PII LEAK DETECTED in log [%s]: pattern '%s' still matches.",
                scrubbed_log.log_id[:8], pattern_name,
            )
    return leaks


def validate_batch(
    scrubbed_logs: List[ScrubbedAgentLog],
) -> Dict[str, List[str]]:
    """
    Validate an entire batch of scrubbed logs.

    Returns:
        Dict mapping log_id → list of leaking pattern names.
        Only logs with leaks appear in the output.
        Empty dict = all clean.
    """
    report: Dict[str, List[str]] = {}
    for log in scrubbed_logs:
        leaks = validate_scrubbed_log(log)
        if leaks:
            report[log.log_id] = leaks

    if report:
        logger.error(
            "PII VALIDATION FAILED: %d log(s) contain residual PII.",
            len(report),
        )
    else:
        logger.info("PII validation passed: all %d logs clean.", len(scrubbed_logs))

    return report