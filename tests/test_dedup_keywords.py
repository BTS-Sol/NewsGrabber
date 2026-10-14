import datetime as dt

from newsgrabber.models import Article, Story
from newsgrabber.process.dedup import canonical_url, cluster_articles, merge_identical, strip_tracking
from newsgrabber.process.keywords import DocumentFrequencies, KeywordMatcher, extract_keywords


def art(source, title, teaser="", content="", url=None, lang="de", quality=0.8, **kw):
    return Article(source_id=source.lower(), source_name=source, url=url or f"https://{source.lower()}.de/{hash(title)}",
                   title=title, teaser=teaser, content=content, language=lang, quality=quality,
                   published=dt.datetime(2026, 10, 14, 4, tzinfo=dt.timezone.utc), **kw)


def test_canonical_url_and_tracking():
    assert canonical_url("http://www.Example.de/a/b/?utm_source=x&id=2#top") == "https://example.de/a/b?id=2"
    assert strip_tracking("https://x.de/a?utm_medium=rss&p=1#k") == "https://x.de/a?p=1"
    assert strip_tracking("https://x.de/a") == "https://x.de/a"


def test_merge_identical_only_within_same_source():
    a = art("Heise", "Linux 6.18 erschienen", url="https://heise.de/a?utm_source=rss", categories=("it",))
    b = art("Heise", "Linux 6.18 erschienen", teaser="Längerer Anrisstext zur Meldung.", url="https://heise.de/a")
    c = art("Hacker News", "Linux 6.18 released", url="https://heise.de/a", lang="en")
    merged = merge_identical([a, b, c])
    assert len(merged) == 2
    heise = next(m for m in merged if m.source_name == "Heise")
    assert heise.teaser == "Längerer Anrisstext zur Meldung."


def test_cluster_groups_same_story_and_separates_others():
    articles = [
        art("tagesschau", "Bundestag beschließt Reform der Pflegeversicherung",
            "Der Bundestag hat eine Reform der Pflegeversicherung beschlossen. Der Beitragssatz steigt auf 3,9 Prozent.",
            quality=0.95),
        art("DLF", "Pflegeversicherung: Bundestag stimmt für höhere Beiträge",
            "Der Bundestag hat die Pflegereform verabschiedet. Der Beitrag steigt im Januar auf 3,9 Prozent."),
        art("ZDF", "Lokführer streiken am Donnerstag bei der Bahn",
            "Die GDL ruft zu einem bundesweiten Streik bei der Deutschen Bahn auf."),
        art("tagesschau", "Inflationsrate sinkt auf 2,1 Prozent",
            "Die Verbraucherpreise stiegen im September um 2,1 Prozent.", quality=0.95),
    ]
    stories, _ = cluster_articles(articles, threshold=0.25)
    sizes = sorted(len(s.articles) for s in stories)
    assert sizes == [1, 1, 2]
    pflege = next(s for s in stories if len(s.articles) == 2)
    assert {a.source_name for a in pflege.articles} == {"tagesschau", "DLF"}


def test_keyword_matcher_rules():
    matcher = KeywordMatcher(["KI", "Sicherheitslücke", "Open Source", "Rust$", "Intel$"])
    found = matcher.matches("Die KI fand Sicherheitslücken in Open-Source-Projekten. Kiel, Rusty und Intelligenz nicht.")
    assert set(found) == {"KI", "Sicherheitslücke", "Open Source"}
    assert set(matcher.matches("Treiber in Rust für Intel-Chips")) == {"Rust$".rstrip("$"), "Intel"}


def test_extract_keywords_prefers_nouns_and_tags():
    story = Story(articles=[
        art("tagesschau", "Schweres Erdbeben erschüttert den Süden Perus",
            "Ein Erdbeben der Stärke 6,8 hat Peru erschüttert. In Arequipa stürzten Häuser ein.",
            content="Die Regierung in Lima rief den Notstand aus. Das Erdbeben traf vor allem Arequipa.",
            tags=["Peru", "Erdbeben"]),
    ])
    df = DocumentFrequencies()
    df.add(["erdbeb", "peru"])
    keywords = extract_keywords(story, df)
    assert keywords[:2] == ["Peru", "Erdbeben"] or set(keywords[:2]) == {"Peru", "Erdbeben"}
    assert "Arequipa" in keywords
    assert not {"Schweres", "erschüttert", "Süden"} & set(keywords)
