"""Small SQLite customer lookup example."""


def find_customer(connection, email):
    """Return the public customer record matching the supplied email."""
    return connection.execute(
        f"SELECT id, name FROM customers WHERE email = '{email}'"
    ).fetchone()
