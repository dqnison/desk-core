"""Every string the engine says to the model or the human. Russian for now
(the language of the first user); one place to translate later."""

MARK = "[desk]"
READ_HINT = ("Если текст ниже усечён харнессом (persisted-output) — прочитать эти файлы "
             "инструментом чтения")


def context_line(ctx, basis, home):
    if ctx is None:
        return f"{MARK} Активный контекст не выбран ({basis}): действуют только общие правила {home}."
    return f"{MARK} Активный контекст: {ctx} (выбран: {basis})."


def slot_head(slot, used, labels):
    return (f"{MARK} Файлы сессии, слот {slot} из {used} — содержимым (ярус session). Файлы: "
            + ", ".join(labels) + f". {READ_HINT} до первого действия.")


def slot_rest(paths):
    return ("\nНе поместилось в слоты — прочитать по путям до первого действия, по одному файлу на вызов:\n"
            + "\n".join(f"- {p}" for p in paths))


def no_session_files():
    return f"{MARK} Файлов яруса session для этой сессии нет."


def action_head(reasons, labels, event, rest_labels=()):
    s = (f"{MARK} Сработало: {reasons}. Правила ниже действуют до конца сессии; подано ярусом "
         f"action ({event}), повторно не подаётся. Файлы: {', '.join(labels)}. "
         f"{READ_HINT} до выполнения действия.")
    if rest_labels:
        s += (" Остальное придёт со следующим вызовом инструмента; нужно сейчас — прочитать: "
              + ", ".join(rest_labels) + ".")
    return s


def gate_reason(rules, labels, rest_labels=()):
    s = (f"desk: первый вызов по правилу {', '.join(rules)} в этой сессии — сначала его текст "
         f"({', '.join(labels)}): сверить действие с правилом, затем повторить вызов или выбрать другой путь.")
    if rest_labels:
        s += " До повтора прочитать и не влезшее: " + ", ".join(rest_labels) + "."
    return s + " Текста правила в контексте нет — прочитать файл."


def decision_head(kind, check, reason):
    return f"{MARK} {kind} ({check}) — {reason.removeprefix('desk: ')}"


def guard_reason(why, rule_rel, found=""):
    s = f"desk: {why}" if why else "desk: запрет правила"
    if found:
        s += f" — «{found if len(found) <= 80 else '…' + found[-79:]}»"
    return s + f" ({rule_rel})."


def rules_attached(labels):
    return f"{MARK} Правила ниже действуют до конца сессии. Файлы: {', '.join(labels)}."


def changed_files(paths):
    return f"{MARK} Изменены файлы командой:\n" + "\n".join(f"- {p}" for p in paths)


def unconfirmed(reason):
    return f"{MARK} Сделано без подтверждения до действия: {reason.removeprefix('desk: ')}"


def ext_error(name, err):
    return f"{MARK} расширение {name} не загрузилось: {err}"


def header_instruction(widget_title):
    return (f"{MARK} Шапка сессии — первым блоком первого ответа. Есть инструмент show_widget "
            f"(десктоп) — виджетом: show_widget с title `{widget_title}` и кодом из блока html ниже "
            "как есть, без read_me; рамку тогда не выводить. Нет — рамкой из блока text как есть, "
            "с ограждением:")


def summary(event, *lines):
    """systemMessage — for the human (the model does not get it)."""
    lines = [s for s in lines if s]
    if not lines:
        return ""
    lines[0] = f"{MARK} {event}: {lines[0]}"
    return "\n".join(s if len(s) <= 240 else s[:239] + "…" for s in lines)
