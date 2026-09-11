"""Exercise real widget cleanup in a disposable, offscreen Qt process."""

from __future__ import annotations

import argparse
import gc

from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QWidget
from shiboken6 import delete, getCppPointer, isValid

from src.gui.app_dialogs import ask_integer, ask_text
from src.gui.views.startup import StartupProgressCard
from src.gui.widget_style import install_combo_box_popup_coordinator


def exercise_dialog(application: QApplication, parent: QWidget, case: str) -> None:
    accept = case.endswith("accept")

    def finish() -> None:
        dialog = application.activeModalWidget()
        assert isinstance(dialog, QDialog)
        if accept:
            dialog.accept()
        else:
            dialog.reject()

    QTimer.singleShot(0, finish)
    if case.startswith("integer"):
        assert ask_integer(parent, "Loop", "Count", 2, 2, 999) == (2, accept)
    else:
        assert ask_text(parent, "Save", "Name", text="probe") == ("probe", accept)


def exercise_startup_card() -> None:
    card = StartupProgressCard()
    card.show()
    card.set_progress(50, "Loading", "")
    card.close()


def exercise_combo() -> None:
    combo = QComboBox()
    # Include cyclic collection, not only immediate reference-count cleanup.
    combo.reference_cycle = combo
    combo.addItems(["left", "right"])
    combo.show()
    combo.showPopup()
    combo.hidePopup()
    combo.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("case", choices=(
        "integer_accept", "integer_reject", "text_accept", "text_reject", "startup", "combo",
    ))
    case = parser.parse_args().case
    gc.disable()  # Collect at explicit checkpoints in this subprocess only.
    application = QApplication([])
    application.setQuitOnLastWindowClosed(False)
    removed: list[bool] = []
    screen = application.primaryScreen()
    assert screen is not None
    native_address = getCppPointer(screen)
    screen.destroyed.connect(lambda: removed.append(True))
    del screen  # A retained wrapper would conceal premature native destruction.
    parent = QWidget()
    if case == "combo":
        install_combo_box_popup_coordinator(application)

    def assert_screen_alive() -> None:
        # Do not call native screen methods after observing premature deletion.
        assert not removed, f"{case}: QScreen destroyed before QApplication exit"
        current = application.primaryScreen()
        assert current is not None and isValid(current)
        assert getCppPointer(current) == native_address
        assert current.availableGeometry().isValid()

    for _ in range(12):
        # An application-level query may keep the shared wrapper alive while a
        # dialog/combobox borrows it. Release it before collecting the widget.
        borrowed_screen = application.primaryScreen()
        if case == "startup":
            exercise_startup_card()
        elif case == "combo":
            exercise_combo()
        else:
            exercise_dialog(application, parent, case)
        assert isValid(borrowed_screen), f"{case}: shared screen wrapper invalidated"
        del borrowed_screen
        gc.collect()
        assert_screen_alive()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        gc.collect()
        assert_screen_alive()
        application.processEvents()
        assert_screen_alive()

    del parent
    gc.collect()
    assert_screen_alive()
    # Qt must still release its own screen on normal application destruction.
    delete(application)
    assert removed == [True]
    print(f"{case}: 12 cleanup cycles and application teardown passed", flush=True)


if __name__ == "__main__":
    main()
