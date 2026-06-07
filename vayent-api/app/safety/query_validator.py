"""Query safety and validation module."""
import re
import logging
from typing import Tuple, List

from app.config import get_settings

logger = logging.getLogger(__name__)

# Destructive query patterns
DANGEROUS_KEYWORDS = {
    "INSERT", "UPDATE", "DELETE", "DROP", "CREATE",
    "ALTER", "TRUNCATE", "REPLACE", "EXEC", "EXECUTE",
    "MERGE", "GRANT", "REVOKE", "COPY", "CALL", "DO",
    "LOAD", "ATTACH", "DETACH", "VACUUM", "ANALYZE",
    "LOCK", "UNLOCK",
}

# Read-only keywords
READONLY_KEYWORDS = {"SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH"}
BLOCKED_PATTERNS = [
    (r";\s*\S", "Multiple SQL statements are not allowed"),
    (r"--", "Inline SQL comments are not allowed"),
    (r"/\*", "Block SQL comments are not allowed"),
    (r"\bINTO\s+OUTFILE\b", "Exporting query results is not allowed"),
    (r"\bINTO\s+DUMPFILE\b", "Exporting query results is not allowed"),
    (r"\bLOAD_FILE\s*\(", "Reading server-side files is not allowed"),
    (r"\bPG_READ_FILE\s*\(", "Reading server-side files is not allowed"),
    (r"\bPG_LS_DIR\s*\(", "Listing server-side files is not allowed"),
    (r"\bLO_IMPORT\s*\(", "Server-side file import is not allowed"),
    (r"\bPG_TERMINATE_BACKEND\s*\(", "Administrative database functions are not allowed"),
    (r"\bPG_CANCEL_BACKEND\s*\(", "Administrative database functions are not allowed"),
    (r"\bPG_RELOAD_CONF\s*\(", "Administrative database functions are not allowed"),
    (r"\bDBLINK_\w*\s*\(", "Cross-database link functions are not allowed"),
    (r"\bPG_SLEEP\s*\(", "Sleep functions are not allowed"),
    (r"\bSLEEP\s*\(", "Sleep functions are not allowed"),
    (r"\bBENCHMARK\s*\(", "Benchmark functions are not allowed"),
    (r"\bGET_LOCK\s*\(", "Advisory lock functions are not allowed"),
    (r"\bFOR\s+UPDATE\b", "Row-locking reads are not allowed"),
    (r"\bFOR\s+SHARE\b", "Row-locking reads are not allowed"),
    (r"\bLOCK\s+IN\s+SHARE\s+MODE\b", "Row-locking reads are not allowed"),
    (r"\bEXPLAIN\s+ANALYZE\b", "EXPLAIN ANALYZE is not allowed"),
]


class QueryValidator:
    """Validates SQL queries for safety."""

    @staticmethod
    def is_destructive(query: str) -> bool:
        """Check if query is destructive (modifies data)."""
        normalized = query.strip().upper()
        if not normalized:
            return False

        words = re.findall(r"[A-Z_]+", normalized)
        if not words:
            return False

        if words[0] in DANGEROUS_KEYWORDS:
            return True

        if words[0] == "WITH":
            return any(word in DANGEROUS_KEYWORDS for word in words[1:])

        if words[0] == "EXPLAIN":
            return any(word in DANGEROUS_KEYWORDS for word in words[1:])

        return False

    @staticmethod
    def get_query_type(query: str) -> str:
        """Get the type of SQL query."""
        normalized = query.strip().upper()
        normalized = re.sub(r'--.*$', '', normalized, flags=re.MULTILINE)
        normalized = re.sub(r'/\*.*?\*/', '', normalized, flags=re.DOTALL)

        keywords = normalized.split()
        if keywords:
            return keywords[0]
        return "UNKNOWN"

    @staticmethod
    def validate_query(query: str) -> Tuple[bool, List[str], str]:
        """
        Validate query for common SQL injection patterns.

        Returns: (is_valid, warnings, error_message)
        """
        if not query or not query.strip():
            return False, [], "Query is empty"

        settings = get_settings()
        normalized = query.strip()
        upper = normalized.upper()

        if len(normalized) > settings.max_query_length:
            return False, [], "Query exceeds maximum allowed length"

        warnings = []

        for pattern, warning in BLOCKED_PATTERNS:
            if re.search(pattern, upper):
                return False, [], warning

        # Check for obvious SQL injection patterns
        dangerous_patterns = [
            (r"1\s*=\s*1", "Potential SQL injection: tautology detected"),
            (r"'\s*OR\s*'", "Potential SQL injection: OR condition detected"),
            (r"UNION.*SELECT", "Potential SQL injection: UNION-based pattern detected"),
        ]

        for pattern, warning in dangerous_patterns:
            if re.search(pattern, upper):
                warnings.append(warning)

        # Check for unmatched quotes
        single_quotes = query.count("'") - query.count("\\'")
        double_quotes = query.count('"') - query.count('\\"')

        if single_quotes % 2 != 0:
            return False, [], "Unmatched single quotes detected"
        if double_quotes % 2 != 0:
            return False, [], "Unmatched double quotes detected"

        query_type = QueryValidator.get_query_type(query)
        if query_type == "UNKNOWN":
            return False, [], "Could not determine query type"

        if query_type not in READONLY_KEYWORDS and query_type not in DANGEROUS_KEYWORDS:
            return False, [], f"Unsupported query type: {query_type}"

        if query_type == "EXPLAIN" and QueryValidator.is_destructive(query):
            return False, [], "Unsafe EXPLAIN statements are not allowed"

        return True, warnings, ""

    @staticmethod
    def extract_tables_from_query(query: str) -> List[str]:
        """
        Extract table names from SQL query.
        This is a simple regex-based approach.
        """
        tables = []

        # Pattern for FROM clause
        from_pattern = r'FROM\s+([a-zA-Z0-9_\.]+)'
        from_matches = re.findall(from_pattern, query, re.IGNORECASE)
        tables.extend(from_matches)

        # Pattern for UPDATE clause
        update_pattern = r'UPDATE\s+([a-zA-Z0-9_\.]+)'
        update_matches = re.findall(update_pattern, query, re.IGNORECASE)
        tables.extend(update_matches)

        # Pattern for DELETE FROM clause
        delete_pattern = r'DELETE\s+FROM\s+([a-zA-Z0-9_\.]+)'
        delete_matches = re.findall(delete_pattern, query, re.IGNORECASE)
        tables.extend(delete_matches)

        # Pattern for INSERT INTO clause
        insert_pattern = r'INSERT\s+INTO\s+([a-zA-Z0-9_\.]+)'
        insert_matches = re.findall(insert_pattern, query, re.IGNORECASE)
        tables.extend(insert_matches)

        # Remove duplicates while preserving order
        seen = set()
        unique_tables = []
        for table in tables:
            # Remove schema prefix if present
            table_name = table.split('.')[-1] if '.' in table else table
            if table_name.lower() not in seen:
                seen.add(table_name.lower())
                unique_tables.append(table_name)

        return unique_tables


class QuerySafety:
    """High-level query safety checking."""

    @staticmethod
    def check_query_safety(query: str) -> dict:
        """
        Comprehensive safety check for a query.

        Returns: {
            is_safe: bool,
            is_destructive: bool,
            query_type: str,
            affected_tables: List[str],
            warnings: List[str],
            error: Optional[str]
        }
        """
        # Validate query
        is_valid, warnings, error = QueryValidator.validate_query(query)

        return {
            "is_safe": is_valid and len(warnings) == 0,
            "is_destructive": QueryValidator.is_destructive(query),
            "query_type": QueryValidator.get_query_type(query),
            "affected_tables": QueryValidator.extract_tables_from_query(query),
            "warnings": warnings,
            "error": error,
        }


# Singleton instance
query_validator = QueryValidator()
query_safety = QuerySafety()
