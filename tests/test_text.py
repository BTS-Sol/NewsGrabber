from newsgrabber.process.clean import clean_content, clean_teaser, clean_title, html_to_text
from newsgrabber.process.text import detect_language, split_sentences, truncate_words, word_count


def test_split_sentences_handles_abbreviations_and_ordinals():
    text = ("Die Regierung, u. a. das Finanzministerium, plant z. B. neue Regeln. Am 3. Oktober ist Feiertag. "
            "Der 2. Weltkrieg endete 1945. Dr. Müller sagte: „Wir sind bereit.“ Danach folgte die Abstimmung!")
    assert split_sentences(text) == [
        "Die Regierung, u. a. das Finanzministerium, plant z. B. neue Regeln.",
        "Am 3. Oktober ist Feiertag.",
        "Der 2. Weltkrieg endete 1945.",
        "Dr. Müller sagte: „Wir sind bereit.“",
        "Danach folgte die Abstimmung!",
    ]


def test_split_sentences_uses_paragraphs_and_english():
    assert split_sentences("Erste Zeile ohne Punkt\nZweite Zeile. Dritter Satz.") == [
        "Erste Zeile ohne Punkt", "Zweite Zeile.", "Dritter Satz."]
    assert split_sentences("The U.S. Senate voted. Mr. Smith left early.") == [
        "The U.S. Senate voted.", "Mr. Smith left early."]


def test_detect_language():
    assert detect_language("Der Bundestag hat am Dienstag über die Reform der Rente beraten und abgestimmt.") == "de"
    assert detect_language("The committee said the new rules would apply to all of the companies.") == "en"


def test_word_count_and_truncate():
    assert word_count("Linux 6.18 ist da – mit neuen Treibern.") == 7
    assert truncate_words("eins zwei drei vier", 2) == "eins zwei …"
    assert truncate_words("eins zwei", 5) == "eins zwei"


def test_html_to_text_keeps_paragraphs_and_drops_scripts():
    html = "<div><p>Erster <b>Absatz</b>.</p><script>var x=1;</script><p>Zweiter&nbsp;Absatz.</p></div>"
    assert html_to_text(html) == "Erster Absatz.\nZweiter Absatz."


def test_clean_title_removes_source_suffix():
    assert clean_title("Neue Regeln für Drohnen | tagesschau.de", "tagesschau") == "Neue Regeln für Drohnen"
    assert clean_title("Linux 6.18 ist da - heise online", "heise online") == "Linux 6.18 ist da"
    assert clean_title("+++ Liveticker zur Wahl +++") == "Liveticker zur Wahl"


def test_clean_teaser_removes_read_more_and_title():
    assert clean_teaser("<p>Text der Meldung. <a href='x'>mehr</a></p>") == "Text der Meldung."
    assert clean_teaser("Der Text geht weiter [&#8230;]") == "Der Text geht weiter"
    assert clean_teaser("Titel der Meldung: Der eigentliche Text.", "Titel der Meldung") == "Der eigentliche Text."
    assert clean_teaser("<p>Inhalt hier.</p><p>The post Inhalt appeared first on Blog.</p>") == "Inhalt hier."


def test_clean_content_removes_boilerplate_and_duplicates():
    text = ("Erster Absatz mit Inhalt der Meldung.\nNewsletter abonnieren\nMehr zum Thema\nStartseite\n"
            "Erster Absatz mit Inhalt der Meldung.\nZweiter Absatz mit weiteren Details zur Lage.")
    assert clean_content(text) == "Erster Absatz mit Inhalt der Meldung.\nZweiter Absatz mit weiteren Details zur Lage."
