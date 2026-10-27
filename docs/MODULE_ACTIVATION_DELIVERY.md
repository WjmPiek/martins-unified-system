# Activation code delivery

Admin can search Module Activations by holder name, registered sign-in email,
branch name or franchise code. After confirming payment, select the holder,
module and registered recipient, then Generate and Email Activation Code.
The status row retains the code and delivery result. Retry email resends the
same code; reissue replaces and cancels earlier unused codes for that module.
Codes remain single-use and bound to the existing owner principal and module.

The actual sign-in identity is captured on new module requests. Its email is
preferred when it still has approved access. Older requests prefer a unique
approved linked identity; multiple identities require Admin to select one.
Only currently authorised sign-ins can receive a code. Changing the franchise
business contact email does not change any sign-in identity.

Subscriptions remains visible under Activated Modules. Paid active modules are
shown in Active subscriptions and removed from available choices. Trials can
still request paid activation. Once all optional modules are paid and active,
the empty selection form is replaced with an all-active message. Manuals stay
permanently free.

Migration v139 preserves legacy code hashes. Legacy codes still redeem normally,
but cannot be displayed or emailed unless reissued because their raw values were
never stored. New raw codes are encrypted with Fernet and verified against the
existing hash on retrieval. Admin responses use Cache-Control: no-store.

Encryption uses MODULE_CODE_ENCRYPTION_KEY (a Fernet key) when configured,
otherwise a domain-separated key derived from the existing Flask SECRET_KEY.
Keep the chosen key stable across instances and releases. Changing it makes
stored codes unavailable for display/resend; their hashes still allow holders
to redeem previously delivered values. Do not print keys or raw codes in logs.

Delivery uses the existing Flask-Mail SMTP settings and the registration mail
connection's bounded timeout. A successful send means the mail server accepted
the message, not proof of arrival in an inbox. Failure leaves the saved code
available and records a generic retryable error. Deployments do not send old
codes or change module permissions automatically.
