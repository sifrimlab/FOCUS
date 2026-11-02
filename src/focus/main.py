"""Command-line bootstrap for GUI and batch pipeline execution."""

from focus.config.main_cli import build_main_parser

import logging


def main() -> None:
    """Parse command-line arguments and execute the requested workflow."""
    parser = build_main_parser()
    args = parser.parse_args()

    if args.config:
        # CLI mode: load config, validate, run pipeline
        from focus.config.keys import ConfigParameters
        from focus.config.command_file import load_command_config
        import focus.runtime.logging as runtime_logging
        import focus.pipeline.runner as pipeline

        # Phase 1: console-only logging so validation errors are formatted
        runtime_logging.setup_logging(debug=args.debug)
        logger = logging.getLogger("focus")

        config_path = args.config
        config = load_command_config(config_path, logger)

        # Phase 2: full logging — adds the file handler now that dataset_path is known
        runtime_logging.setup_logging(
            config[ConfigParameters.DATASET_PATH], debug=args.debug
        )
        logger.info(f"Config loaded and validated: {config_path}")

        pipeline.run(config)
    else:
        # GUI mode: start web interface
        from focus.gui.server import MainGUI

        gui = MainGUI(debug=args.debug)
        gui.start()


if __name__ == "__main__":
    main()
