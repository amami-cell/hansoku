"""URL と画面の対応を、実ブラウザで確かめる。

状態が URL に出ていないと、戻るボタンでアプリごと抜け、リンクも送れず、
店長は自分の店をホーム画面に置けない。ここが壊れると誰も気づかないまま
「使いにくいアプリ」になるので、実際にブラウザを動かして押さえる。

chromium が無い環境（CIの一部）では自動で飛ばす。
"""
import functools, http.server, os, pathlib, shutil, socketserver, sys, tempfile, threading

CHROME = os.environ.get("HANSOKU_CHROME") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
if not pathlib.Path(CHROME).exists():
    print(f"chromium が無いので飛ばします（{CHROME}）")
    raise SystemExit(0)
try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("playwright が無いので飛ばします")
    raise SystemExit(0)

HERE = pathlib.Path(__file__).resolve().parent
REPO = HERE.parent.parent
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="hansoku-route-")) / "site"
shutil.copytree(REPO / "web", ROOT)
(ROOT / "data").mkdir(exist_ok=True)
shutil.copy(HERE / "fixtures" / "dashboard_preview.json", ROOT / "data" / "dashboard.json")
H = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))
class Q(socketserver.TCPServer): allow_reuse_address = True
srv = Q(("127.0.0.1", 8794), H); threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = "http://127.0.0.1:8794/"
fails = []
def ok(name, cond, got=""):
    print(("  ok   " if cond else "  FAIL ") + name + ("" if cond else f"   → {got}"))
    if not cond: fails.append(name)

with sync_playwright() as pw:
    b = pw.chromium.launch(executable_path=CHROME)
    ctx = b.new_context(viewport={"width":390,"height":844})
    pg = ctx.new_page()
    pg.goto(BASE, wait_until="networkidle"); pg.wait_for_timeout(600)

    print("URLに状態が出る")
    ok("最初は #/", pg.evaluate("() => location.hash") in ("#/", ""), pg.evaluate("()=>location.hash"))
    pg.evaluate("() => go({kind:'store', code:'1006'})"); pg.wait_for_timeout(300)
    ok("店舗詳細で #/store/1006", pg.evaluate("() => location.hash") == "#/store/1006", pg.evaluate("()=>location.hash"))
    pg.evaluate("() => go({kind:'campaigns'})"); pg.wait_for_timeout(300)
    ok("施策一覧で #/campaigns", pg.evaluate("() => location.hash") == "#/campaigns", pg.evaluate("()=>location.hash"))

    print("戻るボタンが効く")
    pg.go_back(); pg.wait_for_timeout(400)
    ok("戻ると店舗詳細へ", pg.evaluate("() => VIEW.kind") == "store", pg.evaluate("()=>VIEW.kind"))
    ok("画面も店舗詳細", "この店の販促" in pg.content())
    pg.go_back(); pg.wait_for_timeout(400)
    ok("もう一度戻ると全店", pg.evaluate("() => VIEW.kind") == "schedule", pg.evaluate("()=>VIEW.kind"))
    pg.go_forward(); pg.wait_for_timeout(400)
    ok("進むで店舗詳細", pg.evaluate("() => VIEW.kind") == "store", pg.evaluate("()=>VIEW.kind"))

    print("URLを直接開ける（リンクを送れる）")
    pg2 = ctx.new_page()
    pg2.goto(BASE + "#/store/1015", wait_until="networkidle"); pg2.wait_for_timeout(700)
    ok("#/store/1015 で開く", pg2.evaluate("() => VIEW.code") == "1015", pg2.evaluate("()=>JSON.stringify(VIEW)"))
    pg2.goto(BASE + "#/campaigns?status=review", wait_until="networkidle"); pg2.wait_for_timeout(700)
    ok("絞り込みつきURLが効く", pg2.evaluate("() => CAMP_FILTER.status") == "review", pg2.evaluate("()=>JSON.stringify(CAMP_FILTER)"))
    n = pg2.evaluate("() => document.querySelectorAll('.clist li').length")
    ok("絞り込まれている（84件ではない）", 0 < n < 84, f"{n}件")
    pg2.goto(BASE + "#/store/9999", wait_until="networkidle"); pg2.wait_for_timeout(600)
    ok("存在しない店でも落ちない", pg2.evaluate("() => typeof VIEW.kind") == "string")
    pg2.goto(BASE + "#/でたらめ", wait_until="networkidle"); pg2.wait_for_timeout(600)
    ok("読めないURLは全店に落ちる", pg2.evaluate("() => VIEW.kind") == "schedule", pg2.evaluate("()=>VIEW.kind"))
    pg2.close()

    print("店舗詳細の先頭サマリ（店長が5秒で見るもの）")
    pg4 = ctx.new_page()
    pg4.goto(BASE + "#/store/1006", wait_until="networkidle"); pg4.wait_for_timeout(700)
    html = pg4.content()
    ok("直近確定月の数字が先頭にある", ".ssm-big" in html or "月の売上" in html)
    ok("当月がまだ出ない理由を書いている", "締め後" in html)
    top = pg4.evaluate("() => { const e = document.querySelector('.ssm-sec');"
                       "  return e ? Math.round(e.getBoundingClientRect().top + window.scrollY) : 99999; }")
    ok("販促が最初の1画面に近い（1200px下ではない）", top < 900, f"{top}px")
    ok("細かい数字は畳んである", pg4.evaluate("() => !!document.querySelector('.moredet')"))
    ok("畳んだ中に期間合計がある",
       pg4.evaluate("""() => { const d = document.querySelector('.moredet');
         return !!d && d.textContent.includes('期間合計'); }"""))
    # サマリの行から施策詳細へ飛べるか（押せる見た目なら押せること）
    has_row = pg4.evaluate("() => !!document.querySelector('.ssm-list li[data-camp]')")
    if has_row:
        pg4.evaluate("() => document.querySelector('.ssm-list li[data-camp]').click()")
        pg4.wait_for_timeout(400)
        ok("サマリの行から施策詳細へ飛べる", pg4.evaluate("() => VIEW.kind") == "campaign",
           pg4.evaluate("()=>VIEW.kind"))
    else:
        ok("サマリの行から施策詳細へ飛べる（対象行なし・確認省略）", True)
    pg4.close()

    print("うちの店を覚える")
    pg3 = ctx.new_page()
    pg3.goto(BASE + "#/store/1015", wait_until="networkidle"); pg3.wait_for_timeout(700)
    pg3.evaluate("() => document.querySelector('[data-home]').click()"); pg3.wait_for_timeout(400)
    ok("★になる", "★ うちの店" in pg3.content())
    pg3.goto(BASE, wait_until="networkidle"); pg3.wait_for_timeout(700)
    ok("次に開くと自分の店", pg3.evaluate("() => VIEW.code") == "1015", pg3.evaluate("()=>JSON.stringify(VIEW)"))
    ok("URLも #/store/1015", pg3.evaluate("() => location.hash") == "#/store/1015", pg3.evaluate("()=>location.hash"))
    pg3.evaluate("() => document.querySelector('[data-home]').click()"); pg3.wait_for_timeout(400)
    pg3.goto(BASE, wait_until="networkidle"); pg3.wait_for_timeout(700)
    ok("解除すると全店に戻る", pg3.evaluate("() => VIEW.kind") == "schedule", pg3.evaluate("()=>VIEW.kind"))
    b.close()
srv.shutdown()
shutil.rmtree(ROOT.parent, ignore_errors=True)
print("\n" + ("すべて通過" if not fails else f"{len(fails)}件失敗: {fails}"))
sys.exit(1 if fails else 0)
