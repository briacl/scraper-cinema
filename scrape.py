#!/usr/bin/env python3
"""
scrape.py
Fait une requête vers l'URL fournie et sauvegarde le HTML dans le dossier d'exécution.

Remarques:
- Utilise un User-Agent explicite indiquant qu'il s'agit d'un scraper.
- Si la requête échoue, écrit l'erreur dans `filmes_error.txt`.
"""
from pathlib import Path
import sys
import argparse
import json
from datetime import datetime
import requests
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse
import re
import time

# URL par défaut (peut être surchargée via la ligne de commande)
DEFAULT_URL = "https://www.cinemas/nos.pt/filmes"


def make_output_path_from_url(url: str) -> Path:
    """Crée un nom de fichier de sortie simple à partir de l'URL pour éviter d'écraser d'autres fichiers."""
    # Remplace les caractères non alphanumériques par des underscore
    safe = "".join(c if c.isalnum() else "_" for c in url)
    return Path(f"{safe}.html")


OUTPUT = Path("filmes.html")
ERROR_FILE = Path("filmes_error.txt")

HEADERS = {
    # User-Agent plus détaillé (contient le contact demandé)
    "User-Agent": "MyScraper/1.0 (+briac.le.meillat@gmail.com) - scraping for research"
}


def main():
    parser = argparse.ArgumentParser(description="Simple scraper: récupère une page HTML et la sauvegarde sur disque.")
    parser.add_argument("url", nargs="?", help="URL à scrapper (si omise, une saisie interactive sera proposée)")
    args = parser.parse_args()

    # Si l'URL n'a pas été fournie en argument, demander à l'utilisateur
    if args.url:
        url = args.url
    else:
        try:
            # interactive prompt (utile pour usage direct)
            url = input(f"Entrez l'URL à scrapper (laisser vide pour utiliser la valeur par défaut {DEFAULT_URL}): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()  # newline propre
            url = ""

        if not url:
            url = DEFAULT_URL

    output_path = make_output_path_from_url(url)
    error_path = Path(f"{output_path.stem}_error.txt")

    print(f"Requête vers: {url}")
    print(f"User-Agent: {HEADERS['User-Agent']}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.exceptions.RequestException as exc:
        # Sauvegarde l'erreur sur le disque pour inspection
        error_path.write_text(str(exc), encoding="utf-8")
        print(f"La requête a échoué: {exc}")
        print(f"Détails de l'erreur écrits dans: {error_path.resolve()}")
        sys.exit(1)

    # Écrire le HTML récupéré dans le dossier d'exécution
    output_path.write_text(resp.text, encoding="utf-8")
    print(f"HTML sauvegardé dans: {output_path.resolve()} (taille: {len(resp.text)} octets)")

    # Parser la page et extraire des éléments utiles
    soup = BeautifulSoup(resp.text, "html.parser")

    # Titre: prefere og:title, sinon <title>
    title = None
    og_title = soup.find("meta", property="og:title")
    if og_title and og_title.get("content"):
        title = og_title.get("content").strip()
    elif soup.title and soup.title.string:
        title = soup.title.string.strip()
    else:
        # tentative sur les balises h1 communes
        h1 = soup.find("h1")
        title = h1.get_text(strip=True) if h1 else None

    # Synopsis: meta description ou éléments avec classes communes
    synopsis = None
    meta_desc = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", property="og:description")
    if meta_desc and meta_desc.get("content"):
        synopsis = meta_desc.get("content").strip()
    else:
        # recherche dans les classes contenant des mots-clés
        def find_by_keywords(keywords):
            for kw in keywords:
                el = soup.find(class_=lambda c: c and kw in c.lower())
                if el:
                    return el.get_text(" ", strip=True)
            return None

        synopsis = find_by_keywords(["synopsis", "synop", "resume", "résumé", "resume", "description", "summary"]) or None

    # Horaires / séances: collecte de textes dans classes pouvant contenir ces infos
    showtimes = []
    for kw in ["seance", "seances", "horaire", "horaires", "showtime", "sessions"]:
        els = soup.find_all(class_=lambda c: c and kw in c.lower())
        for el in els:
            text = el.get_text(" ", strip=True)
            if text and text not in showtimes:
                showtimes.append(text)

    # Préparer l'objet JSON
    json_data = {
        "url": url,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
        "title": title,
        "synopsis": synopsis,
        "showtimes": showtimes,
        "html_file": str(output_path.name),
    }

    output_json = output_path.with_suffix(".json")
    output_json.write_text(json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Données extraites sauvegardées dans: {output_json.resolve()}")

    # --- Extraction des fiches films listées sur la page (AlloCiné) ---
    # Helper: extraction d'une carte film
    def extract_film_card(card, base_url):
        title_tag = card.find("a", class_="meta-title-link")
        title = title_tag.get_text(strip=True) if title_tag else None
        rel_link = title_tag["href"] if title_tag and title_tag.has_attr("href") else None
        link = urljoin(base_url, rel_link) if rel_link else None

        img = card.find("img", class_="thumbnail-img")
        poster = None
        if img:
            poster = img.get("data-src") or img.get("src")
            if poster:
                poster = urljoin(base_url, poster)

        date_el = card.find("span", class_="date")
        release_date = date_el.get_text(strip=True) if date_el else None

        duration = None
        info_el = card.find("div", class_="meta-body-item meta-body-info")
        if info_el:
            txt = info_el.get_text(" ", strip=True)
            m = re.search(r"(\d+h\s*\d*min|\d+h|min)", txt)
            if m:
                duration = m.group(0)

        genres = [g.get_text(strip=True) for g in (info_el.find_all("span", class_=lambda c: c and "dark-grey-link" in c) if info_el else [])]

        director = None
        dir_el = card.find("div", class_="meta-body-item meta-body-direction")
        if dir_el:
            d = dir_el.find("span", class_=lambda c: c and "dark-grey-link" in c)
            director = d.get_text(strip=True) if d else dir_el.get_text(" ", strip=True).replace("De", "").strip()

        actors = []
        actor_el = card.find("div", class_="meta-body-item meta-body-actor")
        if actor_el:
            actors = [a.get_text(strip=True) for a in actor_el.find_all("span", class_=lambda c: c and "dark-grey-link" in c)]

        synopsis = None
        syn = card.find("div", class_="synopsis")
        if syn:
            synopsis = syn.get_text(" ", strip=True)

        sessions = None
        btn = card.find("a", href=re.compile(r"/seance/film-"))
        if btn:
            txt = btn.get_text(" ", strip=True)
            m = re.search(r"(\d+[\s\u202f]*\d*)", txt)
            if m:
                sessions = int(m.group(0).replace("\u202f", "").replace(" ", ""))

        ratings = {}
        rating_items = card.find_all("div", class_="rating-item")
        for ri in rating_items:
            title_span = ri.find(class_=re.compile(r"rating-title"))
            note_span = ri.find("span", class_=re.compile(r"stareval-note"))
            if title_span and note_span:
                key = title_span.get_text(strip=True)
                val = note_span.get_text(strip=True)
                ratings[key] = val

        return {
            "title": title,
            "link": link,
            "poster": poster,
            "release_date": release_date,
            "duration": duration,
            "genres": genres,
            "director": director,
            "actors": actors,
            "synopsis": synopsis,
            "sessions": sessions,
            "ratings": ratings,
        }

    # Recherche des cartes film sur la page
    films = []
    base_url = "https://www.allocine.fr"
    cards = soup.find_all("div", class_=lambda c: c and "entity-card" in c)
    if not cards:
        cards = [li.find("div", class_=lambda c: c and "entity-card" in c) for li in soup.find_all("li", class_="mdl")]
        cards = [c for c in cards if c]

    for card in cards:
        films.append(extract_film_card(card, base_url))

    # Pagination: détecter nombre de pages
    pagination = soup.find(class_=lambda c: c and "pagination-item-holder" in c)
    max_page = 1
    pages = []
    if pagination:
        for a in pagination.find_all("a", href=True):
            href = a["href"]
            m = re.search(r"[?&]page=(\d+)", href)
            if m:
                pages.append(int(m.group(1)))
        if pages:
            max_page = max(pages)

    if max_page > 1:
        print(f"Pagination détectée: {max_page} pages — récupération des pages 2..{max_page} (limite 30)")
        limit = min(max_page, 30)
        for p in range(2, limit + 1):
            page_url = urljoin(base_url, urlparse(url).path) + f"?page={p}"
            try:
                time.sleep(0.6)
                r = requests.get(page_url, headers=HEADERS, timeout=15)
                r.raise_for_status()
            except requests.RequestException as exc2:
                print(f"Échec récupération page {p}: {exc2}")
                continue
            soup_p = BeautifulSoup(r.text, "html.parser")
            cards_p = soup_p.find_all("div", class_=lambda c: c and "entity-card" in c)
            for card in cards_p:
                films.append(extract_film_card(card, base_url))

    if films:
        all_json_path = output_path.with_name(output_path.stem + "_all.json")
        all_data = {
            "url": url,
            "fetched_at": datetime.utcnow().isoformat() + "Z",
            "count": len(films),
            "films": films,
        }
        all_json_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Liste complète ({len(films)}) sauvegardée dans: {all_json_path.resolve()}")


if __name__ == "__main__":
    main()
