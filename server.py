import http.server
import urllib.request
import urllib.error
import os
import json
import base64
import io

PORT = int(os.environ.get('PORT', 8081))

def extract_pdf_text(pdf_bytes):
    """Estrae testo da PDF editabile. Ritorna stringa vuota se non c'e' testo."""
    try:
        from pdfminer.high_level import extract_text
        import io
        text = extract_text(io.BytesIO(pdf_bytes), page_numbers=[0])
        # Pulizia — rimuovi righe vuote multiple
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return '\n'.join(lines)
    except Exception:
        return ''

def check_fields_from_text(pdf_text, payload):
    """Verifica campi testuali direttamente dal testo estratto.
    Restituisce un dict con i risultati certi da testo."""
    results = {}
    text_upper = pdf_text.upper()
    text_lines = [l.strip() for l in pdf_text.splitlines() if l.strip()]

    # Cerca il prompt nel payload per estrarre i valori attesi
    prompt_text = ''
    for msg in payload.get('messages', []):
        for block in msg.get('content', []):
            if block.get('type') == 'text':
                prompt_text += block.get('text', '')

    # IPX
    import re
    ipx_match = re.search(r'Valore atteso[:\s]+(IPX\d+)', prompt_text, re.IGNORECASE)
    if ipx_match:
        expected_ipx = ipx_match.group(1).upper()
        found_ipx = re.search(r'IPX\d+', pdf_text, re.IGNORECASE)
        if found_ipx:
            actual_ipx = found_ipx.group(0).upper()
            results['impermeabilita_ok'] = {
                'found': actual_ipx == expected_ipx,
                'note': f'Testo PDF: {actual_ipx} — atteso: {expected_ipx}'
            }

    # ASIN
    asin_match = re.search(r'Valore atteso ESATTO[:\s]+"([A-Z0-9]+)"', prompt_text)
    if asin_match:
        expected_asin = asin_match.group(1)
        if expected_asin in pdf_text:
            results['asin_ok'] = {
                'found': True,
                'note': f'ASIN {expected_asin} trovato nel testo PDF'
            }
        else:
            results['asin_ok'] = {
                'found': False,
                'note': f'ASIN {expected_asin} non trovato nel testo PDF'
            }

    # Lotto
    lot_match = re.search(r'LOT[:\s]*(\d+)', pdf_text, re.IGNORECASE)
    if lot_match:
        results['numero_serie_lotto'] = {
            'found': True,
            'note': f'LOT: {lot_match.group(1)} trovato nel testo PDF'
        }

    # Identificazione fabbricante/importatore
    if 'prodotto e importato da' in pdf_text.lower() or 'imported by' in pdf_text.lower():
        results['indirizzo_importatore'] = {'found': True, 'note': 'Trovato nel testo PDF'}
    # Nome fabbricante — cerca qualsiasi nome azienda (s.r.l., s.p.a., ltd, ecc.)
    if re.search(r's\.r\.l\.|s\.p\.a\.|ltd|gmbh', pdf_text.lower()):
        results['nome_fabbricante'] = {'found': True, 'note': 'Nome azienda trovato nel testo PDF'}
    # Indirizzo fabbricante — cerca pattern indirizzo (via/corso + numero + città)
    if re.search(r'(via|corso|piazza|str\.|street)\s+\w+', pdf_text.lower()):
        results['indirizzo_fabbricante'] = {'found': True, 'note': 'Indirizzo trovato nel testo PDF'}

    # Capacità batteria e tensione nominale
    if 'mah' in pdf_text.lower() and 'tensione nominale' in pdf_text.lower():
        results['valori_nominali'] = {'found': True, 'note': 'Capacità e tensione nominale trovate nel testo PDF'}

    # PAP/CPE smaltimento — legge i codici attesi dal prompt
    scatola_match = re.search(r'smalt_scatola_ok.*?"([^"]+)"', prompt_text, re.IGNORECASE)
    if scatola_match:
        code = scatola_match.group(1).strip()
        # Normalizza: "PAP 21 carta" -> cerca "PAP" e "21"
        parts = re.findall(r'[A-Z]+|\d+', code.upper())
        found = all(p in pdf_text.upper() for p in parts if len(p) > 1)
        if found:
            results['smalt_scatola_ok'] = {'found': True, 'note': f'{code} trovato nel testo PDF'}

    sacchetto_match = re.search(r'smalt_sacchetto_ok.*?"([^"]+)"', prompt_text, re.IGNORECASE)
    if sacchetto_match:
        code = sacchetto_match.group(1).strip()
        parts = re.findall(r'[A-Z]+|\d+', code.upper())
        found = all(p in pdf_text.upper() for p in parts if len(p) > 1)
        if found:
            results['smalt_sacchetto_ok'] = {'found': True, 'note': f'{code} trovato nel testo PDF'}

    doypack_match = re.search(r'smalt_doypack_ok.*?"([^"]+)"', prompt_text, re.IGNORECASE)
    if doypack_match:
        code = doypack_match.group(1).strip()
        parts = re.findall(r'[A-Z]+|\d+', code.upper())
        found = all(p in pdf_text.upper() for p in parts if len(p) > 1)
        if found:
            results['smalt_doypack_ok'] = {'found': True, 'note': f'{code} trovato nel testo PDF'}

    # Ricarica
    ricarica_match = re.search(r'ricarica_ok.*?"([^"]+)"', prompt_text, re.IGNORECASE)
    if ricarica_match:
        expected_ricarica = ricarica_match.group(1).lower()
        if 'magnetica' in expected_ricarica and 'magnetica' in pdf_text.lower():
            # controlla anche il pin se specificato
            if '2 pin' in expected_ricarica or 'minijack' in expected_ricarica:
                pin_found = ('2 pin' in pdf_text.lower() or 'minijack' in pdf_text.lower())
                results['ricarica_ok'] = {
                    'found': pin_found,
                    'note': 'Ricarica magnetica trovata' + (', 2 pin trovato' if pin_found else ', dettaglio pin non trovato nel testo')
                }
            else:
                results['ricarica_ok'] = {'found': True, 'note': 'Modalità ricarica trovata nel testo PDF'}

    return results


def pdf_to_images(pdf_bytes):
    from pdf2image import convert_from_bytes
    from PIL import Image as PILImage

    images = convert_from_bytes(pdf_bytes, dpi=300, first_page=1, last_page=1)
    img = images[0]
    w, h = img.size

    def resize_if_needed(im, max_px=6000):
        iw, ih = im.size
        if iw > max_px or ih > max_px:
            scale = max_px / max(iw, ih)
            im = im.resize((int(iw*scale), int(ih*scale)), PILImage.LANCZOS)
        return im

    def to_b64(im):
        buf = io.BytesIO()
        im.save(buf, format='PNG')
        return base64.b64encode(buf.getvalue()).decode('utf-8')

    ox, oy = int(w * 0.15), int(h * 0.15)
    mx, my = w//2, h//2
    quads = [
        img.crop((0,     0,     mx+ox, my+oy)),
        img.crop((mx-ox, 0,     w,     my+oy)),
        img.crop((0,     my-oy, mx+ox, h)),
        img.crop((mx-ox, my-oy, w,     h)),
    ]
    quads = [resize_if_needed(q, max_px=3000) for q in quads]
    qw = max(q.width for q in quads)
    qh = max(q.height for q in quads)
    grid = PILImage.new('RGB', (qw*2, qh*2), (255,255,255))
    grid.paste(quads[0], (0, 0))
    grid.paste(quads[1], (qw, 0))
    grid.paste(quads[2], (0, qh))
    grid.paste(quads[3], (qw, qh))
    grid = resize_if_needed(grid, max_px=6000)

    return [to_b64(grid)]


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(200)
        self._cors()
        self.end_headers()

    def do_GET(self):
        # Health check per Railway
        self.send_response(200)
        self._cors()
        self.send_header('Content-Type', 'text/plain')
        self.end_headers()
        self.wfile.write(b'OK')

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        header_key = self.headers.get('X-Api-Key', '')
        env_key = os.environ.get('ANTHROPIC_API_KEY', '')
        api_key = env_key or (header_key if header_key != 'managed' else '')

        pdf_text = ''
        text_results = {}
        try:
            payload = json.loads(body)
            for msg in payload.get('messages', []):
                new_content = []
                for block in msg.get('content', []):
                    if block.get('type') == 'document' and block.get('source', {}).get('media_type') == 'application/pdf':
                        pdf_bytes = base64.b64decode(block['source']['data'])
                        pdf_text = extract_pdf_text(pdf_bytes)
                        if pdf_text:
                            # Check campi testuali lato server — affidabile 100%
                            text_results = check_fields_from_text(pdf_text, payload)
                            # Manda solo il testo al modello per i campi rimanenti
                            new_content.append({
                                'type': 'text',
                                'text': f'TESTO ESTRATTO DAL PDF:\n{pdf_text}'
                            })
                        # Converti sempre in immagine per simboli grafici
                        images = pdf_to_images(pdf_bytes)
                        for img_b64 in images:
                            new_content.append({
                                'type': 'image',
                                'source': {
                                    'type': 'base64',
                                    'media_type': 'image/png',
                                    'data': img_b64
                                }
                            })
                    else:
                        new_content.append(block)
                msg['content'] = new_content
            body = json.dumps(payload).encode('utf-8')
        except Exception as e:
            print(f'Errore conversione PDF: {e}')

        req = urllib.request.Request(
            'https://api.anthropic.com/v1/messages',
            data=body,
            headers={
                'Content-Type': 'application/json',
                'x-api-key': api_key,
                'anthropic-version': '2023-06-01',
            },
            method='POST'
        )
        try:
            with urllib.request.urlopen(req) as resp:
                result = resp.read()
            # Se ci sono risultati da testo, li inietta nel JSON della risposta
            if text_results:
                try:
                    resp_json = json.loads(result)
                    for item in resp_json.get('content', []):
                        if item.get('type') == 'text':
                            text = item['text']
                            start = text.find('{')
                            end = text.rfind('}')
                            if start != -1 and end != -1:
                                inner = json.loads(text[start:end+1])
                                results = inner.get('results', inner)
                                results.update(text_results)
                                inner['results'] = results
                                item['text'] = json.dumps(inner)
                                result = json.dumps(resp_json).encode('utf-8')
                                break
                except Exception as e:
                    print(f'  Errore merge risultati testo: {e}')
            self.send_response(200)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(result)
        except urllib.error.HTTPError as e:
            error_body = e.read()
            self.send_response(e.code)
            self._cors()
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(error_body)

    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS, GET')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type, X-Api-Key')

    def log_message(self, format, *args):
        print(f'{args[1]} {self.path}')


os.chdir(os.path.dirname(os.path.abspath(__file__)))

import threading
FILE_PORT = 8080
if os.environ.get('RAILWAY_ENVIRONMENT') is None:
    class FileHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, format, *args): pass
    file_server = http.server.HTTPServer(('', FILE_PORT), FileHandler)
    threading.Thread(target=file_server.serve_forever, daemon=True).start()
    print(f'Apri Chrome su: http://localhost:{FILE_PORT}/compliance-checker.html')

print(f'Proxy API avviato su porta {PORT}')
print('Lascia aperta questa finestra.')
http.server.HTTPServer(('', PORT), ProxyHandler).serve_forever()
