import nox

nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["run"]  # default session


@nox.session
def run(session):
    session.install(".")
    session.run("gamdam", *session.posargs)


@nox.session
def typing(session):
    session.install(".")
    session.install("mypy", "trio-typing[mypy]")
    session.run("mypy", *session.posargs, "gamdam")
