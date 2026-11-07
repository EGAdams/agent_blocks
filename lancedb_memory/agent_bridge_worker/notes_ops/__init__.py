"""The Bridge `notes` agent's operations, one Command object each.

    requests.py    what a notes task may say (pydantic, the boundary)
    base.py        the shared Protocols, errors and outcome builders; NotesContext (a Facade over the collaborators)
    edits.py       INoteEdit Strategies (find/replace, line, section) + edit_from(), their Factory
    highlights.py  IHighlightChange Strategies (add, recolor, clear) + highlight_from()
    commands.py    INotesCommand Commands, a Template Method for verified writes, and the CommandRegistry
"""
