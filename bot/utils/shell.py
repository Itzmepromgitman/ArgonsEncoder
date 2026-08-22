# Developed by ARGON telegram: @REACTIVEARGON
import io

from bot.config import OWNER_ID


# Function to execute Python code (owner-gated at both plugin and util level)
async def run_python_code(code: str):
    try:
        local_vars = {}
        output_buffer = io.StringIO()
        exec_globals = {
            "__builtins__": globals()["__builtins__"],
            "print": lambda *args, **kwargs: print(*args, **kwargs, file=output_buffer),
        }

        exec(code, exec_globals, local_vars)

        output = output_buffer.getvalue().strip()
        if not output:
            output = local_vars.get("output", "Executed successfully with no output.")

        return output
    except Exception as e:
        return f"Error: {e}"


async def shell_command(client, message):
    if message.from_user is None or message.from_user.id != OWNER_ID:
        await message.reply_text("❌ Owner only.")
        return

    if not message.reply_to_message:
        return await message.reply(
            "Reply to a message containing Python code or a .py file."
        )

    if message.reply_to_message.document:
        file = await message.reply_to_message.download()
        with open(file, "r", encoding="utf-8", errors="replace") as f:
            code = f.read()
    else:
        code = message.reply_to_message.text or ""

    if not code.strip():
        return await message.reply("Nothing to execute.")

    response = await run_python_code(code)
    await message.reply(f"<b>Output:</b>\n<code>{response[:3500]}</code>", quote=True)
