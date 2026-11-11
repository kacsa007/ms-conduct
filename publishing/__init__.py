"""Source-linked publication draft generator for the conduct dossier."""

from .writer import ArticleDraft, ArticleError, ArticleOptions, build_article

__all__ = ["ArticleDraft", "ArticleError", "ArticleOptions", "build_article"]
__version__ = "1.0.0"
