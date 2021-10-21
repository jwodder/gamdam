import nox

nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["run"]  # default session


@nox.session
def run(session):
    session.install("-r", "requirements.txt")
    session.run("python", "gamdam.py", *session.posargs)


@nox.session
def typing(session):
    session.install("-r", "requirements.txt")
    session.install("mypy", "trio-typing[mypy]")
    session.run("mypy", *session.posargs, "gamdam.py")
