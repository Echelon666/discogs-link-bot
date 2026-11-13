DISCOGS LINK BOT — PIPELINE V7 — BUILDER WIELOWYNIKOWY + STAGE 2 V6
=============================================================

Punkt wyjścia
-------------
Zmiany są oparte na najnowszym źródle z gałęzi GitHub:
Echelon666/discogs-link-bot
stage2-v6-release-identity-checks
Commit bazowy: be76b8481dbfb5f0417f4fcd851f841b960f4cb1

Plik discogs_bot_verify_v6.py z dostarczonego ZIP-a był identyczny z plikiem
na tej gałęzi. Aktualizacje w tym pakiecie są lokalnymi zmianami względem
tego źródła; nie zostały wysłane (push) do GitHuba.

Co się zmieniło
---------------
1. Stage 1: discogs_barcode_builder.py
   - nadal czyta MBID-y z sekcji POSSIBILITIES -> ALBUM w report.txt;
   - nadal pobiera dane MusicBrainz przez lokalny Web Service
     (domyślnie http://localhost:5000);
   - wyszukuje Discogs po kodzie kreskowym i pobiera wszystkie strony wyników;
   - dokładnie jeden wynik: zachowuje ten jeden release bez rozwijania wersji;
   - więcej niż jeden wynik: zachowuje wszystkie wyniki wyszukiwania, a następnie
     pobiera wszystkie wersje z każdego powiązanego Master przez API
     /masters/{master_id}/versions, ze wszystkimi stronami paginacji;
   - usuwa powtórzone Discogs Release ID w obrębie tego samego MBID;
   - zapisuje jeden wiersz na kandydata w stage1_v6_candidates.tsv.

   Endpoint /masters/{master_id}/versions jest API-owym odpowiednikiem
   rozwinięcia „View all versions” na stronie Discogs.

2. Stage 2: discogs_bot_verify_v6.py
   - zachowuje kontrole V6: medium, rok, kraj, artysta, tytuł, barcode,
     label, catalog number, istniejące relacje i blacklistę;
   - medium/rok/kraj porównuje tylko wtedy, gdy MusicBrainz ma dla danego pola
     użyteczną informację;
   - brak informacji w MB oznacza NOT_PRESENT_IN_MUSICBRAINZ i pominięcie
     wyłącznie tej kontroli;
   - jeżeli MB ma wartość, Discogs nadal musi dostarczyć zgodną wartość;
     brak, konflikt, niejednoznaczność lub niemożliwe mapowanie w Discogs
     nadal daje MANUAL, a nie automatyczne dopasowanie.

3. run_discogs_bot_verify_v6.ps1
   - domyślnie pobiera stage1_v6_candidates.tsv jako wejście Stage 2 V6.

Bez PostgreSQL i psql
---------------------
Stage 1 korzysta z lokalnego MusicBrainz Web Service (/ws/2), tak jak
oryginalny aktywny builder z ZIP-a. Nie używa PostgreSQL ani psql.

Uruchomienie
------------
Umieść obok skryptów plik report.txt oraz upewnij się, że lokalny MusicBrainz
mirror działa pod http://localhost:5000 (albo podaj --mb-base-url).

A. Pilotaż Stage 1 — pierwsze 50 unikalnych MBID:
   py .\discogs_barcode_builder.py .\report.txt --limit 50 --reset

   Program poprosi o token Discogs (ukryte wpisywanie), o ile DISCOGS_TOKEN
   nie jest już ustawiony w środowisku.

B. Weryfikacja pilota — pierwszych 50 kandydatów Stage 2:
   .\run_discogs_bot_verify_v6.ps1 -Limit 50

C. Dalsze przetwarzanie Stage 1:
   py .\discogs_barcode_builder.py .\report.txt

   Limit 0 oznacza wszystkie MBID-y. Program wznowi pracę z pliku postępu.
   Cache API jest zachowywany. Aby świadomie rozpocząć od nowa, użyj --reset.

D. Pełna weryfikacja Stage 2:
   .\run_discogs_bot_verify_v6.ps1 -Limit 0

Ważne pliki wyjściowe Stage 1
-----------------------------
discogs_barcode_builder_v6_output\stage1_v6_candidates.tsv
    Bezpośrednie wejście dla Stage 2 V6; jeden wiersz na kandydata.

discogs_barcode_builder_v6_output\stage1_v6_candidates.json
    Szczegółowy zapis grup MBID, wyników wyszukiwania i rozwiniętych wersji.

discogs_barcode_builder_v6_output\stage1_v6_summary.txt
    Podsumowanie liczby wyników, rozszerzonych masterów i kandydatów.

Postęp i cache Stage 1
----------------------
discogs_barcode_builder_v6_progress.jsonl
    Postęp per MBID, umożliwia wznowienie.

discogs_barcode_builder_v6_cache\
    Cache odpowiedzi MusicBrainz i Discogs (ważność domyślnie 6 godzin).

Testy offline
-------------
Z folderu pakietu:
   py -m unittest discover -s .\tests -v

Testy sprawdzają:
- pojedynczy wynik bez rozwijania wersji;
- wiele wyników z rozwinięciem wszystkich Master versions;
- deduplikację release ID i master ID;
- pobieranie wszystkich stron wyników barcode i wszystkich stron versions;
- pomijanie medium/roku/kraju nieobecnych w MusicBrainz;
- zachowanie MANUAL, gdy MB ma wartość, a Discogs nie ma zgodnej informacji;
- zgodność TSV Stage 1 z wejściem Stage 2 V6.

Bezpieczeństwo
--------------
Oba etapy są tylko do odczytu. Nie tworzą ani nie modyfikują relacji w
MusicBrainz i nie wysyłają edycji. Token Discogs nie jest zapisywany w plikach.
Minimalna przerwa między niebuforowanymi żądaniami Discogs wynosi 1,1 sekundy.