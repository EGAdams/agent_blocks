<?php
/**
 * The gateway's interfaces. Every concrete class is named only in
 * Bootstrap.php (the composition root); everything else talks to these.
 *
 * Written for PHP 7.4 syntax on purpose: the website's main PHP is 7.4, and
 * the subdomain this is meant for runs 8.3. No enums, readonly, match,
 * union types, nullsafe operator or named arguments.
 */
declare(strict_types=1);

namespace AgentBridge;

interface IClock
{
    public function now(): int;
}

interface IIdGenerator
{
    public function newId(string $prefix): string;
    public function newSecret(): string;
}

/** Who is calling. Returns null when the credential is missing or wrong. */
interface ITaskAuthenticator
{
    public function authenticate(Request $request): ?Principal;
}

/** Shape-only checks on a submitted envelope. Returns a list of problems. */
interface ITaskValidator
{
    /** @return string[] */
    public function validate(array $body): array;
}

/** Server-side policy. The requested approval_mode is an input, never the answer. */
interface ITaskAuthorizer
{
    public function authorize(Principal $principal, array $envelope): Decision;
}

/** What clients may read and write about tasks. */
interface ITaskRepository
{
    public function insert(array $task): array;
    public function findByIdempotency(string $clientId, string $key): ?array;
    public function find(string $id): ?array;
    /** @return array[] */
    public function events(string $id, int $afterSeq): array;
    public function appendEvent(string $id, string $kind, string $message): void;
    public function result(string $id): ?array;
    /** Returns the task after the request, or null if there is no such task. */
    public function requestCancel(string $id): ?array;
}

/** What workers may do: claim with a lease, keep it alive, and finish. */
interface ITaskLeaseManager
{
    /**
     * The oldest queued task. `$skip` is a lane filter: each entry is `operation@workspace`, and a task matching
     * one is passed over (never claimed, never changed), so a reserved worker is not stuck behind a long task
     * it will not run. Order among the rest is unchanged: created_at, then id.
     * @param string[] $skip
     */
    public function next(array $skip = []): ?array;
    public function claim(string $id, string $workerId, int $leaseSeconds): ?array;
    public function heartbeat(string $id, string $leaseToken, int $leaseSeconds): ?array;
    public function complete(string $id, string $leaseToken, array $result): bool;
    /** Returns the state the task ended in, or null if the lease was not valid. */
    public function fail(string $id, string $leaseToken, string $reason, bool $retryable, bool $cancelled): ?string;
    /** Expired leases go back to queued, or to failed once attempts run out. */
    public function recoverExpired(): int;
}

interface IAgentRegistry
{
    /** @return array[] */
    public function enabledAgents(): array;
    public function agent(string $name): ?array;
}

interface IAuditLogger
{
    public function record(string $actor, string $action, ?string $taskId, string $outcome, array $detail = []): void;
}

/**
 * Content-addressed bytes (scan pages, originals). Found only by a sha256 the
 * server checked on the way in, and checked again on the way out.
 */
interface IBlobStore
{
    /** Stores $bytes under $sha256 after checking they hash to it. Idempotent. */
    public function put(string $sha256, string $bytes): void;
    public function has(string $sha256): bool;
    /** The bytes, or BridgeError when missing or no longer matching their hash. */
    public function read(string $sha256): string;
}

/** Files a browser.download task saved, published by the worker for viewing. */
interface IDownloadStore
{
    /** Replaces every file of $taskId with $files (each carrying its own 'pages'), in one transaction. */
    public function saveTask(string $taskId, array $files): void;
    public function find(string $fileId): ?array;
    /** @return array[] the task's files in order, without their text */
    public function byTask(string $taskId): array;
    /** @return array[] newest first, without their text */
    public function recent(string $clientId, int $limit): array;
    /** @return array[] every rendition of every page */
    public function pages(string $fileId): array;
    public function page(string $fileId, int $page, string $rendition): ?array;
}

/** Scan records uploaded by the worker, each bound to the scan task that made it. */
interface IScanStore
{
    /** Upserts one scan with its pages and texts, in one transaction. */
    public function save(array $scan, array $pages, array $texts): void;
    public function find(string $scanId): ?array;
    public function findByTask(string $taskId): ?array;
    /** Newest first; $before is a scan_id cursor from a previous page. */
    public function recent(string $clientId, int $limit, ?string $before): array;
    /** @return array[] every rendition of every page */
    public function pages(string $scanId): array;
    public function page(string $scanId, int $page, string $rendition): ?array;
    /** @return array[] */
    public function texts(string $scanId, ?int $page): array;
    /** The queued or running scan task for this exact prompt (one per scanner), if any. */
    public function activeStart(string $prompt): ?array;
    public function startsSince(string $clientId, int $since): int;
}

/** One visible step of the submission workflow. Throws BridgeError to stop. */
interface ISubmitPhase
{
    public function name(): string;
    public function run(SubmitContext $context): void;
}

final class Principal
{
    /** @var string */ public $id;
    /** @var string */ public $role;
    /** @var string */ public $label;

    public function __construct(string $id, string $role, string $label)
    {
        $this->id    = $id;
        $this->role  = $role;
        $this->label = $label;
    }
}

final class Decision
{
    /** @var bool */   public $allowed;
    /** @var string */ public $reason;
    /** @var string */ public $effectiveApprovalMode;

    public function __construct(bool $allowed, string $reason, string $effectiveApprovalMode)
    {
        $this->allowed               = $allowed;
        $this->reason                = $reason;
        $this->effectiveApprovalMode = $effectiveApprovalMode;
    }
}

final class BridgeError extends \RuntimeException
{
    /** @var int */    public $status;
    /** @var string */ public $errorCode;

    public function __construct(int $status, string $errorCode, string $message)
    {
        parent::__construct($message);
        $this->status    = $status;
        $this->errorCode = $errorCode;
    }
}

/** State carried through the submit phases. Owned by SubmitWorkflow. */
final class SubmitContext
{
    /** @var Request */        public $request;
    /** @var array|null */     public $body = null;
    /** @var Principal|null */ public $principal = null;
    /** @var Decision|null */  public $decision = null;
    /** @var array|null */     public $task = null;
    /** @var bool */           public $replayed = false;
    /** @var string[] */       public $phasesRun = [];

    public function __construct(Request $request)
    {
        $this->request = $request;
    }
}
