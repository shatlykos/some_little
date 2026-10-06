"""Авторизация в Google. Поддерживаются два варианта (определяется по JSON-файлу):

* OAuth-клиент «Desktop app» (основной вариант): один раз входим в браузере под
  аккаунтом календарей (authorize.bat), токен сохраняется в token.json и дальше
  обновляется автоматически.
* Ключ сервисного аккаунта — если в организации разрешено создавать ключи.
"""
from __future__ import annotations

import json
from pathlib import Path

from google.auth.credentials import Credentials
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials

SCOPES = ["https://www.googleapis.com/auth/calendar"]


class AuthError(SystemExit):
    pass


def _read_json(path: Path) -> dict:
    if not path.exists():
        raise AuthError(
            f"Не найден файл {path.name}. Скачайте OAuth-клиент из Google Cloud "
            "(см. README, шаг 2) и положите его в папку бота."
        )
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def is_service_account(credentials_file: Path) -> bool:
    return _read_json(credentials_file).get("type") == "service_account"


def load_credentials(credentials_file: Path, token_file: Path) -> Credentials:
    info = _read_json(credentials_file)
    if info.get("type") == "service_account":
        return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)

    if not token_file.exists():
        raise AuthError("Бот ещё не авторизован в Google. Запустите authorize.bat (README, шаг 3).")
    creds = UserCredentials.from_authorized_user_file(str(token_file), SCOPES)
    if not creds.valid:
        try:
            creds.refresh(Request())
        except Exception as e:  # noqa: BLE001
            raise AuthError(
                f"Не удалось обновить доступ к Google ({e}). "
                "Запустите authorize.bat ещё раз."
            ) from e
        token_file.write_text(creds.to_json(), encoding="utf-8")
    return creds


def authorize(credentials_file: Path, token_file: Path) -> str:
    """Открывает браузер для входа в Google и сохраняет token.json.
    Возвращает email вошедшего аккаунта (если удалось определить)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    info = _read_json(credentials_file)
    if info.get("type") == "service_account":
        raise AuthError("Это ключ сервисного аккаунта — авторизация в браузере не нужна.")
    if "installed" not in info:
        raise AuthError(
            "Это OAuth-клиент не того типа. В Google Cloud создайте клиент "
            "с типом «Desktop app» (Приложение для ПК)."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_file), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",  # гарантирует выдачу refresh-токена
        access_type="offline",
        authorization_prompt_message="Открываю браузер для входа в Google...\n{url}",
        success_message="Готово! Бот получил доступ к календарям. Это окно можно закрыть.",
    )
    token_file.write_text(creds.to_json(), encoding="utf-8")
    return getattr(creds, "account", "") or ""
