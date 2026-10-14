"""Berichtserstellung: Auswahl, Wortbudget und Ausgabe (Markdown, HTML, JSON)."""

from .builder import build_sections, choose_top_items, count_markdown_words, fit_to_budget, plan_lengths, \
    select_stories
from .render import Renderer, briefing_to_dict, output_paths, write_atomic

__all__ = ["Renderer", "briefing_to_dict", "build_sections", "choose_top_items", "count_markdown_words",
           "fit_to_budget", "output_paths", "plan_lengths", "select_stories", "write_atomic"]
