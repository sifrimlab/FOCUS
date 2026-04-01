import os, json, argparse

if __name__ == "__main__":
	parser = argparse.ArgumentParser(
		description='FOCUS: Flexible Multiomics data preprocessing and alignment pipeline.'
	)
	parser.add_argument('-c', '--config', type=str, required=False, default=None,
						help='Absolute path of the JSON config file. If omitted, the GUI starts.')
	args = parser.parse_args()

	if args.config:
		# CLI mode: load config, validate, run pipeline
		from focus.constants import ConfigParameters
		from focus import utils, orchestrator

		config_path = args.config
		if not os.path.exists(config_path):
			raise FileNotFoundError(f"Config file not found: {config_path}")

		try:
			with open(config_path, 'r') as f:
				config = json.load(f)
		except json.JSONDecodeError as e:
			raise ValueError(f"Invalid JSON in config file: {e}")

		config = utils.parse_config(config)

		logger = utils.setup_logging(config[ConfigParameters.DATASET_PATH])
		logger.info(f"Config loaded and validated: {config_path}")

		orchestrator.run(config)
	else:
		# GUI mode: start web interface
		from focus.GUI.main_gui import MainGUI
		gui = MainGUI()
		gui.start()
