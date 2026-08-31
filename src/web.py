from fastapi import FastAPI
from fastapi.responses import HTMLResponse


def create_app(db, status_provider) -> FastAPI:
    app = FastAPI()

    @app.get("/api/status")
    def status():
        return status_provider()

    @app.get("/api/signals")
    def signals(limit: int = 100, symbol: str | None = None):
        return db.recent_signals(limit=limit, symbol=symbol)

    @app.get("/api/stats")
    def stats():
        return db.signal_stats()

    @app.get("/", response_class=HTMLResponse)
    def index():
        return """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Binance Sinyal Ajanı</title>
<style>
body{background:#0d1117;color:#e6edf3;font-family:system-ui;margin:0;padding:16px}
h1{font-size:18px;margin-bottom:2px}table{width:100%;border-collapse:collapse;font-size:13px}
td,th{padding:6px 8px;border-bottom:1px solid #21262d;text-align:left}
.long{color:#3fb950;font-weight:700}.short{color:#f85149;font-weight:700}
#subtitle{color:#8b949e;font-size:13px;margin-bottom:8px}
#status{color:#8b949e;font-size:12px;margin-bottom:12px}
</style></head><body>
<h1>Binance Futures Sinyal Ajanı</h1>
<div id="subtitle">Sinyal Akışı</div>
<div id="status">yükleniyor…</div>
<table><thead><tr><th>Saat</th><th>Sembol</th><th>Yön</th><th>Dilim</th>
<th>Skor</th><th>Sonuç</th><th>R</th><th>Kurallar</th></tr></thead><tbody id="rows"></tbody></table>
<script>
const SONUC = {'OPEN':'AÇIK','STOPPED':'STOP','TARGET':'HEDEF','EXPIRED':'SÜRE'};
async function refresh(){
  const [s, sig] = await Promise.all([
    fetch('/api/status').then(r=>r.json()),
    fetch('/api/signals?limit=100').then(r=>r.json())]);
  document.getElementById('status').textContent =
    `${s.symbols} sembol | WS: ${s.ws ? 'bağlı' : 'KOPUK'} | sinyaller: ${sig.length}`;
  document.getElementById('rows').innerHTML = sig.map(x => {
    const hits = JSON.parse(x.hits_json).map(h=>h.detail).join('; ');
    const d = x.direction === 'LONG' ? 'long' : 'short';
    const st = x.status || 'OPEN';
    const r = x.result_r == null ? '-' : Number(x.result_r).toFixed(2);
    return `<tr><td>${new Date(x.ts).toLocaleTimeString()}</td><td>${x.symbol}</td>
    <td class="${d}">${x.strong ? 'GÜÇLÜ ' : ''}${x.direction}</td><td>${x.timeframe}</td>
    <td>${x.score}</td><td>${SONUC[st] || st}</td><td>${r}</td><td>${hits}</td></tr>`;}).join('');
}
refresh(); setInterval(refresh, 3000);
</script></body></html>"""

    return app
