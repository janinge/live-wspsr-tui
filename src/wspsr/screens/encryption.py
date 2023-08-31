from textual import on
from textual.app import ComposeResult
from textual.screen import Screen
from textual.containers import Container, Vertical
from textual.widgets import Header, Footer, Static, Input, Button
from textual.validation import Length, ValidationResult, Validator

ENCRYPTION_INSTRUCTIONS = """\
The transcriptions will be encrypted before being written to any USB stick. To use \
password based encryption for this, specify a strong passphrase twice in the boxes bellow. \
It will not be possible to retrieve or modify this passphrase later.

This passphrase will also be added to the list of possible decryption keys.

To use asymmetric encryption instead, insert a USB stick now with a PGP/GPG public key \
using the filename public.gpg in its root folder.
"""


class SetEncryptionKeyScreen(Screen):
    BINDINGS = [("x", "reboot", "Restart computer")]

    def __init__(
        self,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name, id, classes)
        self.password_validator = EqualityValidator("The two password fields are not equal.")

    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Static(ENCRYPTION_INSTRUCTIONS, classes="information"),
            Vertical(
                Input(placeholder="Enter a password...", password=True, id="password_first",
                      validators=[
                          Length(minimum=8,
                                 failure_description="Password must be longer than 8 characters.")
                      ]),
                Input(placeholder="And repeat it here.", password=True, id="password_confirm",
                      validators=[self.password_validator]),
                classes="passwords"
            ),
            Static("", id="status"),
            Button(label="Next", disabled=True, variant="primary", id="next"),
            id="dialog"
        )
        yield Footer()

    @on(Input.Changed)
    def show_validator_status(self, event: Input.Changed) -> None:
        first_password = self.query_one("#password_first", Input)
        second_password = self.query_one("#password_confirm", Input)

        self.password_validator.update_comparison(first_password.value)

        validation_results = ValidationResult.merge((first_password.validate(first_password.value),
                                                     second_password.validate(second_password.value)))

        if validation_results.is_valid:
            self.query_one("#status", Static).update("")
            self.query_one("#next", Button).disabled = False
        else:
            self.query_one("#status", Static).update(validation_results.failure_descriptions[0])
            self.query_one("#next", Button).disabled = True

    def action_reboot(self) -> None:
        self.app.exit()


class EqualityValidator(Validator):
    def __init__(self, failure_description: str | None = None, comparison_value: str | None = "") -> None:
        super().__init__(failure_description)
        self.comparison_value = comparison_value

    def validate(self, value: str) -> ValidationResult:
        if value == self.comparison_value:
            return self.success()
        else:
            return self.failure(self.failure_description)

    def update_comparison(self, value: str) -> None:
        self.comparison_value = value

