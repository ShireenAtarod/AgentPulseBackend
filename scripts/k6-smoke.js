import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  vus: Number(__ENV.VUS || 2),
  duration: __ENV.DURATION || "30s",
};

const baseUrl = __ENV.BASE_URL || "http://127.0.0.1:8000";
const email = __ENV.AGENTPULSE_EMAIL;
const password = __ENV.AGENTPULSE_PASSWORD;

export default function () {
  const jar = http.cookieJar();
  const login = http.post(
    `${baseUrl}/api/v1/auth/login`,
    JSON.stringify({ email, password }),
    { headers: { "content-type": "application/json", origin: baseUrl }, jar }
  );
  check(login, { "login ok": (r) => r.status === 200 });
  const csrf = login.json("csrf_token");

  const summary = http.get(`${baseUrl}/api/v1/metrics/summary`, { jar });
  check(summary, { "summary ok": (r) => r.status === 200 });

  const sessions = http.get(`${baseUrl}/api/v1/sessions?page=1&page_size=10`, { jar });
  check(sessions, { "sessions ok": (r) => r.status === 200 });

  http.post(`${baseUrl}/api/v1/auth/logout`, "{}", {
    headers: { "content-type": "application/json", "x-csrf-token": csrf, origin: baseUrl },
    jar,
  });
  sleep(1);
}

