"""Shared SDK streaming transport, independent of receptionist control syntax."""
from collections.abc import Callable
from typing import Any
from time import monotonic

from openai import OpenAI

from ...errors import PartialResponseError
from ...models import AgentReply


def complete_chat( client: OpenAI, request: dict[ str, Any ],
                   on_delta: Callable[[ str ], None] | None ) -> AgentReply:
    """Runs on an adapter's worker thread. Always closes the HTTP stream."""
    if on_delta is None:
        response = client.chat.completions.create( **request )
        usage = response.usage
        return AgentReply(
            text=( response.choices[ 0 ].message.content or "" ).strip(),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
        )

    parts: list[ str ] = []
    usage, finish_reason = None, None
    started = monotonic()
    stream = None
    try:
        stream = client.chat.completions.create(
            **request, stream=True, stream_options={ "include_usage": True }
        )
        for chunk in stream:
            if monotonic() - started > 30:
                raise TimeoutError( "Conversation model exceeded its 30 second stream budget." )
            if chunk.usage is not None:
                usage = chunk.usage
            if not chunk.choices:
                continue
            choice = chunk.choices[ 0 ]
            if choice.finish_reason is not None:
                finish_reason = choice.finish_reason
            text = choice.delta.content or ""
            if text:
                parts.append( text )
                on_delta( text )
        if finish_reason != "stop":
            raise PartialResponseError( "The model's response ended before it was complete." )
    except PartialResponseError:
        raise
    except Exception as error:
        if parts:
            # Never retry a partially delivered sentence, mix two providers'
            # replies, or execute a truncated control line as a valid command.
            raise PartialResponseError( "The model's response stream was interrupted." ) from error
        raise
    finally:
        if stream is not None:
            try:
                stream.close()
            except Exception:
                if not parts:
                    raise

    if not "".join( parts ).strip():
        raise RuntimeError( "The model returned no public reply text." )

    return AgentReply( text="".join( parts ).strip(),
                       input_tokens=usage.prompt_tokens if usage else None,
                       output_tokens=usage.completion_tokens if usage else None )
