<?php
declare(strict_types=1);

namespace AgentBridge;

final class Request
{
    /** @var string */ public $method;
    /** @var string */ public $path;
    /** @var array<string,string> lower-cased names */ public $headers;
    /** @var string */ public $body;
    /** @var array<string,string> */ public $query;

    public function __construct(string $method, string $path, array $headers, string $body, array $query = [])
    {
        $this->method  = strtoupper($method);
        $this->path    = $path;
        $this->headers = array_change_key_case($headers, CASE_LOWER);
        $this->body    = $body;
        $this->query   = $query;
    }

    public function header(string $name): string
    {
        $key = strtolower($name);
        return isset($this->headers[$key]) ? (string) $this->headers[$key] : '';
    }

    /** The body as string fields: an HTML form or OAuth token request, or a JSON object. */
    public function form(): array
    {
        if (stripos($this->header('content-type'), 'application/json') === 0) {
            $fields = json_decode($this->body, true);
        } else {
            parse_str($this->body, $fields);
        }
        return is_array($fields) ? array_filter($fields, 'is_string') : [];
    }

    /** Builds a Request from PHP's globals. Apache under CGI/FPM may move the
     *  Authorization header into REDIRECT_HTTP_AUTHORIZATION, so look there too. */
    public static function fromGlobals(string $prefix, int $maxBody): Request
    {
        $headers = [];
        foreach ($_SERVER as $key => $value) {
            if (strpos($key, 'HTTP_') === 0) {
                $headers[str_replace('_', '-', strtolower(substr($key, 5)))] = (string) $value;
            }
        }
        foreach (['CONTENT_TYPE' => 'content-type', 'REDIRECT_HTTP_AUTHORIZATION' => 'authorization'] as $key => $name) {
            if (!isset($headers[$name]) && isset($_SERVER[$key])) {
                $headers[$name] = (string) $_SERVER[$key];
            }
        }
        $uri  = (string) ($_SERVER['REQUEST_URI'] ?? '/');
        $path = (string) parse_url($uri, PHP_URL_PATH);
        if ($prefix !== '' && strpos($path, $prefix) === 0) {
            $path = substr($path, strlen($prefix));
        }
        $body = (string) file_get_contents('php://input', false, null, 0, $maxBody + 1);
        return new Request((string) ($_SERVER['REQUEST_METHOD'] ?? 'GET'), $path === '' ? '/' : $path, $headers, $body, $_GET);
    }
}

final class Response
{
    /** @var int */ public $status;
    /** @var array|null */ public $json;
    /** @var array<string,string> */ public $headers;
    /** @var string|null HTML, for the few pages a person sees */ public $text;

    public function __construct(int $status, ?array $json, array $headers = [])
    {
        $this->status  = $status;
        $this->json    = $json;
        $this->headers = $headers;
    }

    /** A page for a person, locked down: no scripts, no framing, no referrer. */
    public static function html(int $status, string $html, array $headers = []): Response
    {
        $page = new Response($status, null, $headers + [
            'Content-Type'            => 'text/html; charset=utf-8',
            'Content-Security-Policy' => "default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'",
            'X-Frame-Options'         => 'DENY',
            'Referrer-Policy'         => 'no-referrer',
        ]);
        $page->text = $html;
        return $page;
    }

    public static function redirect(string $url): Response
    {
        return new Response(302, null, ['Location' => $url, 'Referrer-Policy' => 'no-referrer']);
    }

    public static function error(int $status, string $code, string $message, array $headers = []): Response
    {
        return new Response($status, ['error' => ['code' => $code, 'message' => $message]], $headers);
    }

    public function emit(): void
    {
        http_response_code($this->status);
        header('Cache-Control: no-store');
        header('X-Content-Type-Options: nosniff');
        foreach ($this->headers as $name => $value) {
            header($name . ': ' . $value);
        }
        if ($this->json !== null) {
            header('Content-Type: application/json');
            echo json_encode($this->json, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
        } elseif ($this->text !== null) {
            echo $this->text;
        }
    }
}
