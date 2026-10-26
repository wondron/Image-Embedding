"""HTTP adapter operations shared by thin endpoint handlers.

These helpers may translate failures into HTTP errors. Model and retrieval
algorithms belong in app.domain, which does not depend on these modules.
"""
