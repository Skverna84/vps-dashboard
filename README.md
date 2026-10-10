# 🚀 VPS Control & Monitoring Dashboard

[![Python](https://img.shields.io/badge/Python-3.8+-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Linux%20(Ubuntu%20%7C%20Debian%20%7C%20CentOS%20%7C%20Rocky)-green.svg)]()
[![RAM Usage](https://img.shields.io/badge/RAM%20Usage-~1.5%20MB-success.svg)]()

> **Ультра-легковесный веб-дашборд для мониторинга ресурсов, управления проектами и анализа безопасности Linux VPS.**
> 
> Разработан без тяжелых фреймворков и сторонних зависимостей (только стандартная библиотека Python 3). Потребляет всего **~1.5 МБ RAM** и 0% CPU в простое. Идеально подходит для маломощных VPS (512 МБ – 1 ГБ RAM).

---

## ⚡ Быстрая установка в 1 команду (Quick Install)

Выполните команду на вашем сервере под `root` (или с `sudo`):

```bash
curl -sSL https://raw.githubusercontent.com/Skverna84/vps-dashboard/main/install.sh | bash
```

Скрипт автоматически:
1. Проверит и при необходимости установит Python 3.
2. Скопирует файлы в `/opt/vps-dashboard/`.
3. Сгенерирует безопасный пароль администратора.
4. Создаст и запустит службу systemd `vps-dashboard.service`.
5. Откроет порт в UFW (если активен).
6. Выведет готовый URL и данные для входа.

### Установка со своими параметрами (Custom Install)

```bash
curl -sSL https://raw.githubusercontent.com/Skverna84/vps-dashboard/main/install.sh | bash -s -- --port 8080 --user admin --pass MySecretPassword123
```

---

## 🌟 Ключевые возможности

* **📊 Мониторинг ресурсов в реальном времени:**
  * Процессор: Load Average (1m, 5m, 15m), процент загрузки CPU, количество ядер.
  * Память RAM: Использовано, свободно, процент занятости с цветным индикатором.
  * Swap: Объем и процент использования файла подкачки.
  * Накопитель: Свободное и занятое место на диске, процент inodes.
  * Uptime: Непрерывное время работы сервера (в днях, часах и минутах).
  * **SSR + Live Polling:** данные сразу пре-рендерятся на сервере и обновляются в браузере каждые 3 секунды.

* **📦 Управление проектами и службами:**
  * Автоматическое распознавание установленных сервисов (RustDesk Server, GoHTTPServer, Docker, Nginx, Fail2ban).
  * Статусы работы (Active / Stopped) с цветными индикаторами.
  * Отображение текущих установленных и доступных версий.
  * Кнопки быстрого управления: `[Перезапуск]`, `[Остановить]`, `[Запустить]`.
  * Автоматическое обновление RustDesk Server до актуального релиза GitHub в 1 клик.

* **🔄 Обновления системы (APT):**
  * Проверка доступных обновлений пакетов безопасности в один клик.
  * Установка обновлений системы (`apt upgrade -y`) с живым выводом в терминал внутри панели.

* **🛡️ Центр безопасности Fail2ban:**
  * Счетчик и список всех заблокированных IP-адресов.
  * Возможность мгновенной разблокировки любого IP в 1 клик.
  * Живая лента последних попыток несанкционированного входа (IP, логин, дата/время).

* **🎨 Современный адаптивный интерфейс (UI/UX):**
  * 🇷🇺 / 🇬🇧 **Двуязычный интерфейс:** переключатель **RU / EN** на лету.
  * 🌙 / ☀️ **Темы оформления:** темная и светлая темы с сохранением в браузере.
  * 📱 **Адаптивный дизайн:** оптимизирован для десктопов, планшетов и мобильных устройств.
  * 🔒 **Безопасность:** защита панели через HTTP Basic Auth и безопасные Cookie.

---

## 📋 Системные требования

* **ОС:** Любой современный Linux (Ubuntu 20.04+, Debian 11+, CentOS 8+, AlmaLinux, Rocky Linux, Fedora).
* **Python:** 3.8 или новее (используется только встроенная библиотека: `http.server`, `json`, `subprocess`).
* **Система инициализации:** systemd.
* **Оперативная память:** от 256 МБ RAM (дашборд занимает менее 2 МБ).

---

## 🛠️ Ручная установка (Manual Installation)

Если вы предпочитаете установить дашборд вручную:

```bash
# 1. Клонируйте репозиторий
git clone https://github.com/Skverna84/vps-dashboard.git /opt/vps-dashboard
cd /opt/vps-dashboard

# 2. Создайте файл конфигурации
cp config.example.json config.json
nano config.json # укажите свой порт, логин и пароль

# 3. Настройте службу systemd
cp vps-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now vps-dashboard.service

# 4. Откройте порт в файрволе (если используется UFW)
ufw allow 8080/tcp comment "VPS Web Dashboard"
```

---

## ⚙️ Управление службой дашборда

```bash
# Проверить статус
systemctl status vps-dashboard

# Перезапустить
systemctl restart vps-dashboard

# Остановить
systemctl stop vps-dashboard

# Просмотреть логи
journalctl -u vps-dashboard -f
```

---

## 🗑️ Удаление (Uninstall)

Для полного удаления панели с сервера выполните:

```bash
curl -sSL https://raw.githubusercontent.com/Skverna84/vps-dashboard/main/uninstall.sh | bash
```

Или из локального каталога:
```bash
bash /opt/vps-dashboard/uninstall.sh
```

---

## 📄 Лицензия

Распространяется под лицензией [MIT](LICENSE). Разрешено свободное использование, модификация и коммерческое применение.
