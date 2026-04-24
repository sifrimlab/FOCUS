import os, sys, json, argparse, logging


def main():
	parser = argparse.ArgumentParser(
		description='FOCUS: Flexible Multiomics data preprocessing and alignment pipeline.'
	)
	parser.add_argument('-c', '--config', type=str, required=False, default=None,
						help='Absolute path of the JSON config file. If omitted, the GUI starts.')
	parser.add_argument('--debug', action='store_true', default=False,
						help='Enable debug logging (shows all log levels including HTTP request logs).')
	args = parser.parse_args()

	if args.config:
		# CLI mode: load config, validate, run pipeline
		from focus.constants import ConfigParameters
		from focus import utils, orchestrator

		# Phase 1: console-only logging so validation errors are formatted
		utils.setup_logging(debug=args.debug)
		logger = logging.getLogger("focus")

		config_path = args.config
		if not os.path.exists(config_path):
			logger.error(f"Config file not found: {config_path}")
			sys.exit(1)

		try:
			with open(config_path, 'r') as f:
				config = json.load(f)
		except json.JSONDecodeError as e:
			logger.error(f"Invalid JSON in config file '{config_path}': {e}")
			sys.exit(1)

		try:
			config = utils.parse_config(config)
		except (TypeError, KeyError, ValueError, FileNotFoundError, PermissionError) as e:
			logger.error(f"Config validation failed: {e}")
			sys.exit(1)

		# Phase 2: full logging — adds the file handler now that dataset_path is known
		utils.setup_logging(config[ConfigParameters.DATASET_PATH], debug=args.debug)
		logger.info(f"Config loaded and validated: {config_path}")

		orchestrator.run(config)
	else:
		# GUI mode: start web interface
		from focus.GUI.main_gui import MainGUI
		gui = MainGUI(debug=args.debug)
		gui.start()


if __name__ == "__main__":
	main()
