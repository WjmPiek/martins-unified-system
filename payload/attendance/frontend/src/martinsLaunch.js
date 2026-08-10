const RETURN_URL_KEY = "martins_attendance_return_url";

function launchEndpoint() {
  const configured = (
    import.meta.env.VITE_API_URL ||
    import.meta.env.VITE_API_BASE_URL ||
    ""
  ).replace(/\/$/, "");

  if (!configured) return "/api/auth/martins-launch";
  return `${configured.endsWith("/api") ? configured : `${configured}/api`}/auth/martins-launch`;
}

export async function consumeMartinsLaunch(token) {
  const response = await fetch(launchEndpoint(), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token }),
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || "Unable to open Attendance from Martins System.");
  }

  if (payload.main_app_url) {
    window.sessionStorage.setItem(RETURN_URL_KEY, payload.main_app_url);
  }

  return payload;
}

export function returnToMartinsIfLaunched() {
  const returnUrl = window.sessionStorage.getItem(RETURN_URL_KEY);
  window.sessionStorage.removeItem(RETURN_URL_KEY);

  if (!returnUrl) return false;
  window.location.assign(returnUrl);
  return true;
}
