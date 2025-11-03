"""Cloudflare Worker entrypoint for proxying S3-compatible storage."""

from .worker import main

__all__ = ["main"]
