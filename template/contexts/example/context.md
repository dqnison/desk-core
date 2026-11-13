---
tier: session
match:
  remote: ['git.example.com']
---

# Контекст: example

Активен, когда `git remote -v` сессии ведёт на `git.example.com` (`match:` выше).
Пример места работы: компания Acme, трекер с ключами вида `DEMO-123`.

- Трекер — [tracker.md](tracker.md), приезжает по вызову CLI трекера.
- Репозитории: `shop-api` — [projects/shop-api/](projects/shop-api/local.md).
- Имена места, которых не должно быть в `process/`, — `place-words.txt`.
