from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from wootify.infrastructure.persistence.unit_of_work import SqlAlchemyUnitOfWork


def test_unit_of_work_commits_and_closes() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table sample (value integer)"))
    factory = sessionmaker(bind=engine)

    with SqlAlchemyUnitOfWork(factory) as unit_of_work:
        unit_of_work.session.execute(text("insert into sample values (1)"))
        unit_of_work.commit()

    with engine.connect() as connection:
        assert connection.execute(text("select value from sample")).scalar_one() == 1


def test_unit_of_work_rolls_back_on_exception() -> None:
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(text("create table sample (value integer)"))
    factory = sessionmaker(bind=engine)

    try:
        with SqlAlchemyUnitOfWork(factory) as unit_of_work:
            unit_of_work.session.execute(text("insert into sample values (1)"))
            raise RuntimeError("fail")
    except RuntimeError:
        pass

    with engine.connect() as connection:
        assert connection.execute(text("select count(*) from sample")).scalar_one() == 0
