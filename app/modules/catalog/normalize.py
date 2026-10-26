import re
import unicodedata


NORMALIZER_VERSION = 1


def normalize_article(value: str) -> str:
    """Normalize common separators only; never guess brand or merge products."""
    ascii_value = unicodedata.normalize("NFKC", value).upper().strip()
    return re.sub(r"[\s\-._/]", "", ascii_value)


def normalize_brand(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).upper().split())


def make_slug(brand_slug: str, article_norm: str) -> str:
    # Cross-language brand names remain stable; public SEO slugs may be curated later.
    clean = re.sub(r"[^a-z0-9-]", "-", brand_slug.lower()).strip("-")
    code = re.sub(r"[^a-z0-9]", "-", article_norm.lower()).strip("-")
    return f"{clean}-{code}"[:180]
