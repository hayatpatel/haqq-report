import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("ic", Path(__file__).parent.parent / "scripts" / "import_credentials.py")
ic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ic)

SAMPLE = """App ID: 111
App Secret: aaa
Short token: zzz
FB_PAGE_ID: 222
FB_PAGE_TOKEN: bbb
Instagram App ID: 333
Insta Access token: ccc
Thread App ID: 444
Thread App Secret: ddd
Thread Access token: eee
App ID\t111
"""


def test_labels_map_to_canonical_names():
    found, conflicts, ignored, _ = ic.parse(SAMPLE)
    assert found == {"FB_APP_ID": "111", "FB_APP_SECRET": "aaa", "FB_PAGE_ID": "222", "FB_PAGE_TOKEN": "bbb",
                     "IG_APP_ID": "333", "IG_TOKEN": "ccc", "TH_APP_ID": "444", "TH_APP_SECRET": "ddd", "TH_TOKEN": "eee"}
    assert not conflicts and ignored == ["Short token"]


def test_conflicting_duplicates_reported():
    _, conflicts, _, _ = ic.parse("App ID: 1\nApp ID: 2\n")
    assert conflicts == {"FB_APP_ID"}


def test_sectioned_services_and_alternate_labels():
    found, _, _, _ = ic.parse("""Telegram: 
API: 111:AAA
Owner ID: 42

Supabase URL: https://x.supabase.co
Supabase secret Key: sb_secret_abc

groq API: gsk_1
Gemini API: AQ.2
""")
    assert found == {"TELEGRAM_BOT_TOKEN": "111:AAA", "TELEGRAM_OWNER_ID": "42",
                     "SUPABASE_URL": "https://x.supabase.co", "SUPABASE_KEY": "sb_secret_abc",
                     "GROQ_API_KEY": "gsk_1", "GEMINI_API_KEY": "AQ.2"}
