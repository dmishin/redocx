"""The command schema: what each command of the language accepts (design §3.2).

`builtin.COMMANDS` declares every command; `bind` checks a parsed command against its
spec and gives its typed arguments.
"""
