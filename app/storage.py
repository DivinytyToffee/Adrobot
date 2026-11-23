from contextlib import contextmanager


class SQLConnection:
    """Use SQLAlchemy's transaction and connection lifecycle with SQLite statements."""
    def __init__(self, engine):
        self.connection = engine.connect()

    def execute(self, statement, parameters=()):
        return self.connection.exec_driver_sql(statement, parameters)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


class Store:
    def __init__(self, connect):
        self.connect = connect

    @contextmanager
    def transaction(self):
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()
