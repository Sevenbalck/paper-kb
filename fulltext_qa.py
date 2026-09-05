"""Secondo canale di Q&A, complementare a graphify_kb.answer_question().

PERCHÉ ESISTE: graphify-ask risponde bene a domande concettuali/relazionali
("come si collega LA a PD-1?") perché naviga un grafo di nodi/edge costruito
per estrazione semantica. Ma è strutturalmente debole su domande fattuali
puntuali ("quale concentrazione di LA è usata nei Treg?", "quanti pazienti nel
cohort 4?") perché quei valori numerici, incastonati in didascalie di figure o
tabelle di metodi, spesso non sopravvivono come attributo di un nodo durante
l'estrazione semantica — restano solo nel testo grezzo del .md, che il grafo
non interroga più una volta costruito.

Questo modulo bypassa il grafo: cerca direttamente nei markdown grezzi in
data/parsed/ (quelli prodotti da Docling, PRIMA di Graphify) e passa a Claude
solo i paragrafi rilevanti trovati, non l'intero corpus. 100% locale per la
ricerca (nessuna chiamata Claude), 2 chiamate Claude per la riformulazione
keyword + sintesi finale — stesso ordine di costo di answer_question().

Non sostituisce graphify-ask: è un fallback esplicito, da usare quando la
domanda è quantitativa/fattuale invece che concettuale. Vedi 'ask-fulltext'
in main.py.
"""
from __future__ import annotations

import re
from pathlib import Path

from utils import call_claude_code, load_config

# Unità di misura comuni nei paper biomedici: un paragrafo che ne contiene
# una insieme a una keyword ha molte più probabilità di contenere la risposta
# a una domanda quantitativa ("quale concentrazione", "quale dose") rispetto
# a un paragrafo che menziona solo il concetto senza numeri.
_UNIT_PATTERN = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:mM|µM|uM|nM|pM|mg/kg|µg|ug|mg|kg|ml|mL|µl|ul|nm|kDa|%|"
    r"days?|hours?|h\b|min(?:utes?)?|°C|x\s?10)",
    re.IGNORECASE,
)

_KEYWORDS_SYSTEM_PROMPT = """Estrai 3-6 parole chiave brevi in INGLESE dalla domanda
dell'utente, adatte a cercare per corrispondenza testuale in paper scientifici in inglese
(la domanda può arrivare in italiano). Preferisci termini tecnici precisi (nomi di geni,
proteine, cellule, tecniche, condizioni sperimentali) piuttosto che parole generiche.
Rispondi SOLO con le parole chiave separate da virgola, nient'altro: niente spiegazioni,
niente numerazione, niente frase introduttiva.

Se è fornita anche una CONVERSAZIONE PRECEDENTE, usala solo per interpretare domande di
follow-up ambigue che fanno riferimento a un turno precedente (es. "e nei topi?" dopo una
domanda sulle cellule umane) — arricchisci le keyword con il contesto mancante, ma
restano sempre keyword per la NUOVA domanda, non un riassunto dello scambio precedente.

Esempio:
Domanda: "quale concentrazione di lattato è usata nelle T Reg?"
Risposta: lactic acid, LA, Treg, concentration, mM"""

_FULLTEXT_ANSWER_SYSTEM_PROMPT = """Rispondi alla domanda dell'utente usando SOLO le
informazioni nei paragrafi forniti come contesto, estratti da paper scientifici. Se il
contesto non contiene la risposta, dillo chiaramente invece di inventare o generalizzare.

Presta particolare attenzione a valori numerici esatti (concentrazioni, dosi, N di
pazienti/campioni, percentuali): riportali esattamente come appaiono nel testo, senza
arrotondare o parafrasare i numeri.

REGOLA sulle citazioni: ogni paragrafo di contesto è preceduto da un tag [fonte: nome_file].
Cita la fonte subito dopo ogni affermazione specifica a cui si riferisce, non una sola volta
alla fine. Se più paragrafi della stessa fonte confermano lo stesso valore, cita comunque
la fonte alla prima menzione. Rispondi in italiano, in prosa discorsiva.

Se è fornita anche una CONVERSAZIONE PRECEDENTE, usala SOLO per capire a cosa si riferisce
la domanda corrente se è un follow-up (es. un pronome, "e per il PD-1?", "quanto invece nei
topi?") e per non ripetere spiegazioni già date. Non è mai una fonte di fatti: se la
conversazione precedente afferma qualcosa che non è confermato nei paragrafi forniti ORA,
non darlo per assunto — verifica sempre contro il contesto PARAGRAFI corrente."""


def _format_history(history: list[dict] | None) -> str:
    """Formatta i turni precedenti come blocco di DATI di riferimento, non
    come trascrizione di dialogo: un testo che somiglia a una vera
    conversazione ("Utente: ... / Assistente: ...") rischia che il modello lo
    interpreti come qualcosa da CONTINUARE invece che da leggere come
    contesto — osservato empiricamente con Claude Code in modalità headless
    (risposte completamente fuori tema, come se stesse rispondendo dentro
    un'altra sessione/progetto). Per questo ogni turno è numerato ed
    etichettato esplicitamente come "già risposto", con un'istruzione a non
    riprenderlo. Va SEMPRE passato via stdin (input_text di
    call_claude_code), mai dentro 'instruction' (argomento -p di 'claude'):
    un'istruzione breve su riga singola lì è collaudata, un blocco
    multi-riga/multi-turno no.

    Tiene solo gli ultimi 'fulltext_chat_max_turns' turni (config.yaml) e
    tronca ogni risposta passata a 'fulltext_chat_max_answer_chars': senza
    questo tetto, una conversazione lunga farebbe crescere senza limite (e in
    proporzione i token consumati da Claude) il testo rimandato a ogni nuovo
    messaggio, solo per interpretare eventuali follow-up — che raramente
    hanno bisogno di più di qualche scambio recente e di poche righe di
    riepilogo per ciascuno. I turni più vecchi/le code delle risposte più
    lunghe vengono scartati silenziosamente: non sono comunque la fonte dei
    fatti (quella resta sempre il contesto PARAGRAFI corrente), solo aiuto a
    capire a cosa si riferisce la domanda nuova."""
    if not history:
        return ""
    cfg = load_config()
    claude_cfg = cfg.get("claude", {})
    max_turns = claude_cfg.get("fulltext_chat_max_turns", 6)
    max_answer_chars = claude_cfg.get("fulltext_chat_max_answer_chars", 500)

    recent = history[-max_turns:] if max_turns else history
    parts = [
        "[CRONOLOGIA CONVERSAZIONE — dati di sola consultazione, già risposti: "
        "NON continuarla, NON rispondere di nuovo a queste domande, usala solo "
        "per capire il contesto della domanda NUOVA più sotto]"
    ]
    for i, turn in enumerate(recent, start=1):
        q = str(turn.get("question", "")).strip()
        a = str(turn.get("answer", "")).strip()
        if max_answer_chars and len(a) > max_answer_chars:
            a = a[:max_answer_chars].rstrip() + "…"
        if q:
            parts.append(f"Domanda precedente {i}: {q}")
        if a:
            parts.append(f"Risposta già data {i}: {a}")
    parts.append("[FINE CRONOLOGIA]")
    return "\n".join(parts)


def _extract_keywords(question: str, model: str, history: list[dict] | None = None) -> list[str]:
    """Riformula la domanda in parole chiave inglesi per il matching testuale.
    🔴 consuma una piccola chiamata Claude (stesso pattern di
    graphify_kb._reformulate_for_graph, ma per ricerca full-text invece che
    per il vocabolario del grafo). 'history' viaggia via stdin — vedi
    _format_history() sul perché non va mai messo dentro l'instruction."""
    raw = call_claude_code(
        instruction=f"DOMANDA ORIGINALE: {question}",
        input_text=_format_history(history),
        system_prompt=_KEYWORDS_SYSTEM_PROMPT,
        model=model,
    )
    keywords = [k.strip() for k in raw.split(",") if k.strip()]
    return keywords or [question]


def _iter_paragraphs(md_path: Path):
    """Divide un markdown in paragrafi (separati da riga vuota), scartando
    quelli troppo corti per essere informativi (titoli, didascalie isolate)."""
    text = md_path.read_text(encoding="utf-8", errors="replace")
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if len(para) >= 40:
            yield para


def _score_paragraph(paragraph: str, keywords: list[str]) -> int:
    """Punteggio euristico: +1 per ogni keyword distinta trovata (case-insensitive,
    match di sottostringa — i nomi di geni/proteine spesso compaiono con
    varianti di maiuscole/pedici persi nella conversione da PDF), +2 bonus se
    il paragrafo contiene anche un valore numerico con unità di misura (il
    segnale più forte per domande tipo 'quale concentrazione/dose/N')."""
    lower = paragraph.lower()
    hits = sum(1 for kw in keywords if kw.lower() in lower)
    if hits == 0:
        return 0
    if _UNIT_PATTERN.search(paragraph):
        hits += 2
    return hits


def search_fulltext(
    question: str,
    target: Path | str | None = None,
    model: str | None = None,
    max_snippets: int = 12,
    max_chars: int = 8000,
    history: list[dict] | None = None,
) -> list[tuple[str, str]]:
    """Cerca nei markdown grezzi i paragrafi più rilevanti per la domanda.
    100% locale (nessuna chiamata Claude oltre all'estrazione keyword).
    Ritorna una lista di tuple (nome_file_senza_estensione, paragrafo),
    ordinata per rilevanza decrescente, troncata a max_snippets e max_chars
    complessivi (qualunque limite viene raggiunto prima).

    'history' (opzionale, lista di {"question", "answer"}) aiuta a
    interpretare domande di follow-up ambigue in fase di estrazione keyword
    — vedi _extract_keywords()."""
    cfg = load_config()
    resolved_model = model or cfg["claude"]["model_summary"]
    target_dir = Path(target) if target is not None else cfg["paths"]["parsed_dir"]

    keywords = _extract_keywords(question, resolved_model, history=history)

    scored: list[tuple[int, str, str]] = []
    for md_path in sorted(target_dir.glob("*.md")):
        for paragraph in _iter_paragraphs(md_path):
            score = _score_paragraph(paragraph, keywords)
            if score > 0:
                scored.append((score, md_path.stem, paragraph))

    scored.sort(key=lambda t: t[0], reverse=True)

    results: list[tuple[str, str]] = []
    total_chars = 0
    for _, source, paragraph in scored:
        if len(results) >= max_snippets or total_chars >= max_chars:
            break
        results.append((source, paragraph))
        total_chars += len(paragraph)
    return results


def answer_from_snippets(
    question: str,
    snippets: list[tuple[str, str]],
    model: str | None = None,
    history: list[dict] | None = None,
) -> str:
    """Sintetizza una risposta da paragrafi GIÀ trovati (es. da search_fulltext()
    chiamata separatamente per mostrarli a video) — usala quando ti serve sia
    ispezionare i paragrafi grezzi sia la risposta finale, per evitare di
    rilanciare la ricerca (e la chiamata Claude di riformulazione keyword che
    comporta) due volte. Per il caso comune "solo risposta" usa
    answer_question_fulltext(), che fa ricerca + sintesi in un solo passo.
    🔴 consuma 1 chiamata Claude (solo la sintesi finale).

    'history' (opzionale, lista di {"question", "answer"} nell'ordine in cui
    sono avvenuti) dà a Claude continuità sui follow-up — vedi nota nel
    system prompt sul perché non è mai trattata come fonte di fatti."""
    cfg = load_config()
    resolved_model = model or cfg["claude"]["model_summary"]

    if not snippets:
        return (
            "Nessun paragrafo pertinente trovato nei markdown grezzi per questa domanda. "
            "Prova a riformulare con termini più specifici (nomi di geni/proteine, "
            "condizioni sperimentali) o verifica che il paper rilevante sia stato ingerito."
        )

    context = "\n\n".join(f"[fonte: {source}]\n{paragraph}" for source, paragraph in snippets)
    input_text = f"PARAGRAFI trovati nei paper (ricerca full-text, non dal grafo):\n\n{context}"
    history_block = _format_history(history)
    if history_block:
        input_text = f"{history_block}\n\n{input_text}"

    answer = call_claude_code(
        instruction=f"DOMANDA (rispondi in italiano a questa): {question}",
        input_text=input_text,
        system_prompt=_FULLTEXT_ANSWER_SYSTEM_PROMPT,
        model=resolved_model,
    )

    sources = sorted({source for source, _ in snippets})
    answer += "\n\n**Paper cercati (full-text) per questa risposta:** " + ", ".join(sources)
    return answer


def answer_question_fulltext(
    question: str,
    target: Path | str | None = None,
    model: str | None = None,
    max_snippets: int = 12,
    history: list[dict] | None = None,
) -> str:
    """Risposta in prosa a una domanda fattuale/quantitativa: ricerca full-text
    (search_fulltext()) + sintesi (answer_from_snippets()) in un solo passo.
    Fallback esplicito per domande su cui graphify-ask (answer_question in
    graphify_kb.py) tende a non trovare nulla di pertinente: valori numerici
    puntuali persi nell'estrazione semantica del grafo.
    🔴 consuma 2 chiamate Claude (estrazione keyword + sintesi finale). Se ti
    serve ANCHE ispezionare i paragrafi grezzi trovati, chiama invece
    search_fulltext() seguita da answer_from_snippets(), per non pagare la
    riformulazione keyword due volte.

    'history' (opzionale, lista di {"question", "answer"}) permette di usare
    questa funzione come turno N+1 di una conversazione: passa i turni
    precedenti perché domande di follow-up ambigue vengano interpretate nel
    contesto giusto, sia in fase di ricerca sia in fase di sintesi."""
    cfg = load_config()
    resolved_model = model or cfg["claude"]["model_summary"]

    snippets = search_fulltext(
        question, target=target, model=resolved_model, max_snippets=max_snippets, history=history
    )
    return answer_from_snippets(question, snippets, model=resolved_model, history=history)


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 2:
        print('Uso: python fulltext_qa.py "<domanda>"')
    else:
        print(answer_question_fulltext(sys.argv[1]))
