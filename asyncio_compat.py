"""Python/Windows 连接已关闭时的 Proactor 清理兼容。请求异常不在这里兜底。"""
import sys


def is_closed_connection_reset(context):
    error = context.get('exception')
    callback = getattr(context.get('handle'), '_callback', None)
    transport = getattr(callback, '__self__', None)
    return (sys.platform == 'win32' and isinstance(error, ConnectionResetError)
            and getattr(error, 'winerror', None) == 10054
            and getattr(callback, '__module__', '') == 'asyncio.proactor_events'
            and getattr(callback, '__name__', '') == '_call_connection_lost'
            and transport is not None and transport.is_closing())


def install_cleanup_handler(loop):
    previous = loop.get_exception_handler()
    def handler(current_loop, context):
        if is_closed_connection_reset(context):
            # 10054 中断了标准库 finally 中的 shutdown；补完余下清理，不能只隐藏异常。
            transport = context['handle']._callback.__self__
            try:
                if transport._sock is not None:
                    transport._sock.close()
                    transport._sock = None
                if transport._server is not None:
                    transport._server._detach()
                    transport._server = None
                transport._called_connection_lost = True
                return
            except Exception as exc:
                context = dict(context, exception=exc, message='Windows 断开连接后的资源清理失败')
        if previous is not None:
            previous(current_loop, context)
        else:
            current_loop.default_exception_handler(context)
    loop.set_exception_handler(handler)
    def restore():
        if loop.get_exception_handler() is handler:
            loop.set_exception_handler(previous)
    return restore
