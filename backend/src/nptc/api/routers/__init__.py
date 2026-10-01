"""API routers. Each module here owns one resource group and is mounted by
``nptc.api.app.create_app``.

House style for every router module: declare the response models in the router module
(or in ``catalogue_shared`` when routers share them), each with ``ConfigDict(frozen=True)``,
and let the return-type annotation drive the response model. Never pass ``response_model=``,
because it can drift from the annotation.
"""
