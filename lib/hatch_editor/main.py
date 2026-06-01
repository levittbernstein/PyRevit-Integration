"""Entry point — run as a standalone app or from PyRevit."""


def run():
    from .ui import HatchApp
    app = HatchApp()
    app.mainloop()


if __name__ == '__main__':
    run()
