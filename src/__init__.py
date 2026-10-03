"""
School Administration System — application package.

Layout
------
``db``      connection handling and error translation
``auth``    login, registration, role-based authorisation
``crud``    insert/update/delete helpers, one module per entity
``reports`` the eight analytical queries from 0004_queries.sql
"""

__all__ = ["db", "auth", "reports"]