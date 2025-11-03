"""Cloudflare Worker entrypoint for the WPlace timelapse proxy."""

from .worker import Default, main

__all__ = ["Default", "main"]

