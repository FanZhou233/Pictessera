"""Desktop user-interface components with lazy imports to avoid service cycles."""

__all__ = ["SettingsDialog"]


def __getattr__(name: str):
    if name == "SettingsDialog":
        from .settings_dialog import SettingsDialog
        return SettingsDialog
    raise AttributeError(name)
