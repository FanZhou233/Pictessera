# Architecture

The project is being migrated from a single-file Qt application to a layered
package. The dependency direction is intentionally one-way:

```text
main.py / UI
    -> services
    -> domain
    -> config

infrastructure -> domain/config
```

- `photo_manager/domain`: framework-independent business objects.
- `photo_manager/services`: reusable business operations such as search.
- `photo_manager/infrastructure`: runtime and concurrency adapters.
- `photo_manager/config.py`: application policy, tuning, and theme values.
- `photo_manager/bootstrap.py`: process setup that must happen before Qt import.
- `tests`: fast tests that do not start the GUI.

`main.py` remains the compatibility entrypoint while UI widgets and media/state
services are migrated incrementally. New business logic should not be added to
`main.py`; it belongs in `domain` or `services`.
