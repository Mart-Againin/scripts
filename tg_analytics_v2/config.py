
# ── Dashboard ──────────────────────────────────────────────────────────────
DASHBOARD_MONTHS_HISTORY = int(os.getenv("DASHBOARD_MONTHS_HISTORY", "6"))

# Фиксированные цвета и имена каналов для дашборда
# Переопределяются через .env как DASHBOARD_CH_<username>=Имя:#ЦВЕТ
def _load_dashboard_channels() -> dict:
    defaults = {
        "@ktsdaily":      {"name": "Программисты",  "color": "#EF4444"},
        "@metaclass":     {"name": "Метакласс",     "color": "#2E7D32"},
        "@kts_specials":  {"name": "Геймификация",  "color": "#EC407A"},
        "@smartbotpro":   {"name": "Смартбот",      "color": "#29ABE2"},
        "@inside_ai_tech":{"name": "Внутри AI",     "color": "#111111"},
        "@kod_v_kaske":   {"name": "Код в каске",   "color": "#FB8C00"},
    }
    # Можно переопределить через .env: DASHBOARD_CH_ktsdaily=Программисты:#EF4444
    result = {}
    for ch, cfg in defaults.items():
        key = "DASHBOARD_CH_" + ch.lstrip("@")
        env_val = os.getenv(key)
        if env_val and ":" in env_val:
            name, color = env_val.rsplit(":", 1)
            result[ch] = {"name": name.strip(), "color": color.strip()}
        else:
            result[ch] = cfg
    return result

DASHBOARD_CHANNELS = _load_dashboard_channels()

# Google Sheets
GOOGLE_PAID_SHEET_URL = os.getenv("GOOGLE_PAID_SHEET_URL", "")

# ── Константы ──────────────────────────────────────────────────────────────
MONTHS_RU = {
    1:"Январь",2:"Февраль",3:"Март",4:"Апрель",5:"Май",6:"Июнь",
    7:"Июль",8:"Август",9:"Сентябрь",10:"Октябрь",11:"Ноябрь",12:"Декабрь"
}
