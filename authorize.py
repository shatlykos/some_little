"""Однократный вход в Google: открывает браузер, сохраняет token.json.

Запуск:  python authorize.py   (или authorize.bat)
"""
import sys

import yaml

sys.stdout.reconfigure(encoding="utf-8")

from bot.config import ROOT  # noqa: E402
from bot.google_auth import AuthError, authorize  # noqa: E402

raw = {}
if (ROOT / "config.yaml").exists():
    with open(ROOT / "config.yaml", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
credentials = ROOT / raw.get("google_credentials", "credentials.json")
token = ROOT / raw.get("google_token", "token.json")

print("Сейчас откроется браузер.")
print("Войдите под аккаунтом, которому принадлежат календари (galaxybook2018@gmail.com).")
print("Если Google напишет «Приложение не проверено» — нажмите «Дополнительно» →")
print("«Перейти на страницу ...» и разрешите доступ к календарю.\n")
try:
    authorize(credentials, token)
except AuthError as e:
    print(f"❌ {e}")
    sys.exit(1)
print(f"\n✅ Готово! Доступ сохранён в {token.name}. Теперь запустите check.bat")
