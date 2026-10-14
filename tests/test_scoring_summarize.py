import datetime as dt

from newsgrabber.config import load_config
from newsgrabber.models import Article, Briefing, BriefingItem, CategorySection, Story
from newsgrabber.process.keywords import DocumentFrequencies
from newsgrabber.rank import Classifier, score_stories
from newsgrabber.report import fit_to_budget
from newsgrabber.storage import History
from newsgrabber.summarize.extractive import ExtractiveSummarizer, shorten_fact
from newsgrabber.process.text import word_count

NOW = dt.datetime(2026, 10, 14, 4, 0, tzinfo=dt.timezone.utc)


def art(title, teaser="", source="tagesschau", hours=1, quality=0.9, categories=("it",), url=None, **kw):
    return Article(source_id=source, source_name=source, url=url or f"https://{source}.de/{abs(hash(title))}",
                   title=title, teaser=teaser, published=NOW - dt.timedelta(hours=hours), quality=quality,
                   categories=tuple(categories), category=categories[0] if len(categories) == 1 else "", **kw)


def test_scoring_prefers_recent_relevant_and_covered_stories():
    config = load_config(None, required=False)
    classifier = Classifier(config)
    fresh = Story([art("Kritische Sicherheitslücke in OpenSSL geschlossen", "Ein Patch behebt die Schwachstelle."),
                   art("OpenSSL: Sicherheitslücke gestopft", "Update verfügbar.", source="golem")])
    old = Story([art("Kritische Sicherheitslücke in Router entdeckt", "Ein Patch fehlt noch.", hours=20)])
    ad = Story([art("Angebot: Rabatt auf Notebooks im Test", "Jetzt Rabatt sichern.")])
    score_stories([fresh, old, ad], config, NOW, classifier)
    assert fresh.score > old.score > ad.score
    assert fresh.topic == "Cybersecurity"
    assert ad.score_parts.get("faktor_negativ") == config.scoring.negative_penalty


def test_mixed_source_is_classified_by_keywords():
    config = load_config(None, required=False)
    classifier = Classifier(config)
    article = art("Bundestag beschließt neues Gesetz zur Rente", "Die Koalition setzte sich durch.",
                  categories=("weltgeschehen", "deutschland"))
    assert classifier.assign_category(article) == "deutschland"
    article = art("Waffenruhe in Nahost: Verhandlungen in Kairo", "Die Vereinten Nationen vermitteln.",
                  categories=("weltgeschehen", "deutschland"))
    assert classifier.assign_category(article) == "weltgeschehen"


def test_history_marks_repeats_and_updates():
    story = Story([art("Linux 6.18 freigegeben", url="https://a.de/1")])
    assert History([("2026-10-13", "x", {"https://a.de/1"}, set())]).classify(story) == "wiederholung"
    story2 = Story([art("Linux 6.18 freigegeben", url="https://a.de/1"), art("Linux 6.18 ist da", url="https://b.de/2")])
    assert History([("2026-10-13", "x", {"https://a.de/1"}, set())]).classify(story2) == "fortsetzung"
    assert History([]).classify(story) == ""


def test_extractive_summary_limits_length_and_source_share():
    sentences = " ".join(f"Satz Nummer {i} enthält eine weitere Information über das Ereignis in Berlin." for i in range(20))
    story = Story([art("Großes Ereignis in Berlin", content=sentences, source="eins", quality=0.9),
                   art("Ereignis in Berlin", teaser="Am Abend gab es 3 weitere Details zum Ereignis in Berlin.",
                       source="zwei", quality=0.8)])
    summarizer = ExtractiveSummarizer(max_words_per_source=40, df=DocumentFrequencies())
    result = summarizer.summarize(story, target_words=60)
    assert word_count(result.summary) <= 75
    assert result.headline == "Großes Ereignis in Berlin"


def test_shorten_fact():
    long = ("Die Regierung hat am Dienstag nach langen Verhandlungen mit den Ländern und Kommunen beschlossen, "
            "dass die Mittel – insgesamt 4,5 Milliarden Euro bis 2030 – in den Ausbau der Netze fließen sollen.")
    assert shorten_fact(long) == "Insgesamt 4,5 Milliarden Euro bis 2030."
    assert shorten_fact("Kurzer Fakt mit 3 Zahlen.") == "Kurzer Fakt mit 3 Zahlen."


def _item(category, rank, score, words=60):
    return BriefingItem(rank=rank, headline=f"Meldung {category} {rank}", summary=". ".join(["Wort " * 9] * (words // 10)) + ".",
                        facts=["Fakt eins mit Zahl 1.", "Fakt zwei mit Zahl 2."], keywords=["A"], sources=[],
                        published=NOW, category=category, score=score, anchor=f"{category}-{rank}")


def test_fit_to_budget_removes_lowest_and_keeps_minimum():
    config = load_config(None, required=False)
    config.report.min_words, config.report.max_words = 300, 600
    sections = [CategorySection(key=c.key, title=c.title,
                                items=[_item(c.key, r, 1.0 - r / 10) for r in range(1, 7)]) for c in config.categories]
    briefing = Briefing(date=NOW.date(), title="T", generated_at=NOW, window_start=NOW, window_end=NOW,
                        sections=sections)

    def count(b):
        return sum(word_count(i.headline + " " + i.summary + " " + " ".join(i.facts)) for i in b.all_items())

    fit_to_budget(briefing, config, count)
    assert count(briefing) <= 600
    assert all(len(s.items) >= 1 for s in briefing.sections)
    for section in briefing.sections:  # die wichtigsten Meldungen bleiben, Ränge lückenlos
        assert [i.rank for i in section.items] == list(range(1, len(section.items) + 1))
        assert section.items[0].headline.endswith(" 1")
