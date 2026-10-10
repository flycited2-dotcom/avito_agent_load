import fcntl
from pathlib import Path
import yaml
from avito_bridge.monitor import Monitor

cfg = yaml.safe_load(Path('config/monitor-main.yaml').read_text())
with Path('state/monitor-main.lock').open('a') as lock:
    fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
    m = Monitor(cfg, None, notify=True)
    m.event('setup-confirmed-20260920', cfg['telegram_bot_id'],
            'Уведомления Авито настроены через этого бота (ID 8673052162).\n'
            'Из XML исключены 3 отсутствующие микроволновки и 5 архивных товаров по вашему подтверждению; всего 8 запретов повторной выгрузки. В публичном XML осталось 188 товаров.\n'
            'Обращения, звонки без разговора и ошибки загрузки: проверка каждые 5 минут. Баланс и расходы: раз в час. Ежедневный отчёт: 09:00 МСК.\n'
            'Остатки IT-партнёров привязаны к 9 сопоставленным объявлениям; обновление фида запускается после свежего снимка склада Симферополя. Других поставщиков не смешиваем с IT-партнёрами.\n'
            'Снятие на площадке произойдёт после обработки обновлённого XML Авито. Платное продвижение автоматически не покупается.')
    m.flush()
    print('pending_notifications', len(m.state['outbox']))
