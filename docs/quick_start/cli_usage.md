# CLI Usage Guide

## Basic CLI Command

```bash
focus --config /path/to/your/focus_config.json
```

## Command Line Interface

The FOCUS CLI provides a non-interactive way to run the complete pipeline using a configuration file.

### Command Syntax

```bash
focus [OPTIONS]
```

### Options

| Option | Description | Required |
|--------|-------------|----------|
| `-c, --config` | Path to JSON configuration file | ✅ Required for CLI mode |
| `-h, --help` | Show help message and exit | ❌ |
| `-v, --verbose` | Enable verbose logging | ❌ |
| `--version` | Show FOCUS version | ❌ |

### Basic Usage Examples

**Run pipeline with configuration file:**
```bash
focus --config /data/project/focus_config.json
```

**Run with verbose logging:**
```bash
focus --config /data/project/focus_config.json --verbose
```

**Show help:**
```bash
focus --help
```

**Show version:**
```bash
focus --version
```

## Configuration File

The CLI requires a valid JSON configuration file. See [Configuration Reference](../configuration/config_structure.md) for complete details.

### Minimum Configuration Example

```json
{
  "dataset_path": "/path/to/dataset",
  "reference_modality": "microscopy",
  "perform_alignment": false,
  "perform_registration": false,
  "huggingface_token": null,
  "spatial_annotations": null,
  "modalities": [
    {
      "alignment_strategy": "manual",
      "name": "microscopy",
      "processing_settings": {
        "color_enhancement": true,
        "remove_background": false,
        "crop_to_tissue": false,
        "gamma": 0.45,
        "force_recomputing": false
      },
      "registration_settings": {},
      "registration_type": "none",
      "type": "microscopy_image"
    }
  ]
}
```

## Running Specific Pipeline Stages

FOCUS processes stages in order, but you can control which stages run:

### Preprocessing Only

```json
{
  "perform_alignment": false,
  "perform_registration": false
}
```

```bash
focus --config preprocessing_only_config.json
```

### Preprocessing + Alignment

```json
{
  "perform_alignment": true,
  "perform_registration": false
}
```

```bash
focus --config align_only_config.json
```

### Full Pipeline

```json
{
  "perform_alignment": true,
  "perform_registration": true
}
```

```bash
focus --config full_pipeline_config.json
```

## Advanced CLI Usage

### Environment Variables

FOCUS respects several environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `FOCUS_LOG_LEVEL` | Logging level (DEBUG, INFO, WARNING, ERROR) | INFO |
| `FOCUS_TEMP_DIR` | Temporary directory | System temp |
| `FOCUS_CACHE_DIR` | Cache directory | `~/.focus/cache` |
| `FOCUS_THREADS` | Number of CPU threads | Auto-detect |

**Example:**
```bash
export FOCUS_LOG_LEVEL=DEBUG
export FOCUS_THREADS=8
focus --config /path/to/config.json
```

### Container CLI Usage

**Docker/Podman:**
```bash
bash focus-container.sh --mount /data/project -- --config /data/project/focus_config.json
```

**Singularity:**
```bash
singularity run --bind /data/project focus.sif --config /data/project/focus_config.json
```

### Windows CLI

```batch
conda activate FOCUS
focus --config C:\data\project\focus_config.json
```

## Monitoring and Logging

### Log Files

FOCUS creates comprehensive log files in your dataset directory:

```
dataset_path/
└── logs/
    ├── focus_pipeline.log        # Main pipeline log
    ├── preprocessing.log         # Preprocessing stage log
    ├── alignment.log             # Alignment stage log
    ├── registration.log         # Registration stage log
    ├── compilation.log           # MuData compilation log
    └── focus_gui.log             # GUI log (if used)
```

### Log Levels

| Level | Description | When to Use |
|-------|-------------|--------------|
| `DEBUG` | Detailed debugging information | Development, troubleshooting |
| `INFO` | Standard operational messages | Normal usage |
| `WARNING` | Potential issues | Monitor for problems |
| `ERROR` | Serious problems | Requires attention |

**Set log level via environment variable:**
```bash
export FOCUS_LOG_LEVEL=DEBUG
focus --config /path/to/config.json
```

**Set log level in configuration:**
```json
{
  "logging_level": "DEBUG"
}
```

### Real-time Monitoring

**Tail the log file:**
```bash
tail -f /path/to/dataset/logs/focus_pipeline.log
```

**Use verbose mode:**
```bash
focus --config /path/to/config.json --verbose
```

## Error Handling and Recovery

### Common Errors

**Error: Configuration file not found**
```
Solution: Check path and permissions
focus --config /correct/path/to/config.json
```

**Error: Invalid JSON configuration**
```
Solution: Validate JSON syntax
python -m json.tool focus_config.json
```

**Error: Dataset path not found**
```
Solution: Verify directory exists
ls /path/to/dataset
```

**Error: Modality directory missing**
```
Solution: Check directory structure matches config
ls /path/to/dataset/sample_001/
```

### Recovery Strategies

**Resume from failure:**
```bash
# Fix the issue
# Run again - FOCUS will skip completed stages
focus --config /path/to/config.json
```

**Force recompute specific stage:**
```json
{
  "modalities": [
    {
      "name": "msi",
      "processing_settings": {
        "force_recomputing": true
      }
    }
  ]
}
```

**Clean and restart:**
```bash
# Remove intermediate files
rm -rf /path/to/dataset/preprocessing/
rm -rf /path/to/dataset/alignment/
rm -rf /path/to/dataset/registration/

# Restart pipeline
focus --config /path/to/config.json
```

## Performance Optimization

### Parallel Processing

Control CPU usage via configuration:

```json
{
  "max_cpu_cores": 8,
  "parallel_samples": 4
}
```

Or environment variable:
```bash
export FOCUS_THREADS=8
focus --config /path/to/config.json
```

### GPU Acceleration

For feature extraction registration, FOCUS automatically uses the first available CUDA GPU. No additional config fields are needed:

```json
{
  "modalities": [
    {
      "name": "microscopy",
      "registration_type": "feature_extraction",
      "registration_settings": {
        "force_recomputing": false
      }
    }
  ]
}
```

## Batch Processing

### Processing Multiple Datasets

```bash
# Create config files for each dataset
for dataset in dataset1 dataset2 dataset3; do
  # Generate or copy config file
  cp template_config.json ${dataset}/focus_config.json
  
  # Update dataset_path in config
  sed -i "s|/path/to/dataset|/data/${dataset}|" ${dataset}/focus_config.json
  
  # Run FOCUS
  focus --config /data/${dataset}/focus_config.json
  
  # Move to next dataset
  echo "Completed ${dataset}"
done
```

### SLURM Batch Script (HPC)

```bash
#!/bin/bash
#SBATCH --job-name=focus_pipeline
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --gres=gpu:1
#SBATCH --time=24:00:00

# Activate conda environment
source /path/to/miniconda/etc/profile.d/conda.sh
conda activate FOCUS

# Run FOCUS
focus --config /scratch/project/focus_config.json

# Check exit status
echo "FOCUS pipeline completed with exit code: $?"
```

Submit with:
```bash
sbatch focus_job.sh
```

## Configuration Management

### Configuration Templates

Create reusable templates:

```bash
# Basic template
cp config_template_basic.json project1/focus_config.json

# Advanced template with all options
cp config_template_advanced.json project2/focus_config.json
```

### Configuration Validation

Validate before running:

```python
import json
from focus.utils import parse_config

# Load config
with open('focus_config.json', 'r') as f:
    config = json.load(f)

# Validate
try:
    parsed_config = parse_config(config)
    print("Configuration is valid!")
except Exception as e:
    print(f"Configuration error: {e}")
```

### Configuration Versioning

Track configuration changes:

```bash
# Initialize git in dataset directory
cd /path/to/dataset
git init
git add focus_config.json
git commit -m "Initial configuration"

# After modifications
git add focus_config.json
git commit -m "Updated processing parameters"
```

## Integration with Other Tools

### Python API

FOCUS can be imported as a Python module:

```python
from focus.orchestrator import run
from focus.utils import parse_config
import json

# Load configuration
with open('focus_config.json', 'r') as f:
    config = json.load(f)

# Parse and validate
config = parse_config(config)

# Run pipeline programmatically
output_files = run(config)

print(f"Generated files: {output_files}")
```

### Jupyter Notebook Integration

```python
# In Jupyter notebook
from focus.orchestrator import run
from focus.utils import parse_config
import json

# Load config
config = json.load(open('focus_config.json'))
config = parse_config(config)

# Run with progress callback
def progress_callback(status):
    print(f"Progress: {status.get('message', '')}")

output_files = run(config, progress_callback=progress_callback)
```

### REST API (Experimental)

FOCUS provides an experimental REST API:

```bash
# Start API server
focus-api --port 8080

# Submit job via API
curl -X POST http://localhost:8080/api/v1/pipeline \
  -H "Content-Type: application/json" \
  -d @focus_config.json

# Check status
curl http://localhost:8080/api/v1/status/<job_id>
```

## Monitoring and Alerting

### Email Notifications

```bash
# Simple email notification on completion
focus --config /path/to/config.json && \
  echo "FOCUS pipeline completed successfully" | mail -s "FOCUS Job Done" user@example.com
```

### Slack Notifications

```bash
# Slack webhook notification
WEBHOOK_URL="https://hooks.slack.com/services/..."

focus --config /path/to/config.json
EXIT_CODE=$?

if [ $EXIT_CODE -eq 0 ]; then
  MESSAGE="✅ FOCUS pipeline completed successfully"
else
  MESSAGE="❌ FOCUS pipeline failed with exit code $EXIT_CODE"
fi

curl -X POST -H 'Content-type: application/json' \
  --data "{"text":"$MESSAGE"}" $WEBHOOK_URL
```

## Best Practices

### Configuration Management

1. **Start with templates**: Use provided configuration templates
2. **Validate first**: Always validate configuration before running
3. **Version control**: Track configuration changes with git
4. **Document changes**: Comment why parameters were changed

### Execution

1. **Test small**: Run with small subset first
2. **Monitor resources**: Watch CPU/RAM/GPU usage
3. **Check logs**: Review logs during and after execution
4. **Validate outputs**: Verify intermediate and final files

### Error Handling

1. **Check dependencies**: Ensure all requirements are met
2. **Review logs**: Start with error messages in logs
3. **Isolate issues**: Test components individually
4. **Report bugs**: Provide complete error information

### Performance

1. **Right-size resources**: Match resources to dataset size
2. **Use caching**: Let FOCUS cache intermediate results
3. **Batch processing**: Process multiple samples efficiently
4. **Monitor progress**: Track execution time and resource usage

## Troubleshooting CLI Issues

### Common Problems

**Issue: Command not found**
```
Solution: Activate conda environment
conda activate FOCUS
```

**Issue: Permission denied**
```
Solution: Check file permissions
chmod +x /path/to/focus
```

**Issue: Module not found**
```
Solution: Reinstall FOCUS
bash install.sh --reinstall
```

**Issue: Out of memory during feature extraction**
```
Solution: Close other GPU processes to free VRAM.
If the problem persists, the GPU does not have enough VRAM for feature_extraction.
```

### Debugging Techniques

**Enable debug logging:**
```bash
export FOCUS_LOG_LEVEL=DEBUG
focus --config /path/to/config.json 2>&1 | tee debug.log
```

**Run specific component:**
```python
from focus.preprocessing import preprocess_modality

result = preprocess_modality(
    path="/path/to/dataset",
    modality_name="microscopy",
    modality_type="microscopy_image",
    preprocessing_settings={}
)
```

**Check intermediate files:**
```bash
# List preprocessing outputs
ls -la /path/to/dataset/preprocessing/

# Check file sizes
du -sh /path/to/dataset/preprocessing/*
```

## CLI vs GUI Comparison

| Feature | CLI | GUI |
|---------|-----|-----|
| **Automation** | ✅ Best | ❌ Limited |
| **Interactive** | ❌ No | ✅ Best |
| **Batch processing** | ✅ Excellent | ❌ Poor |
| **Configuration** | ✅ File-based | ✅ Visual editor |
| **Monitoring** | ❌ Logs only | ✅ Real-time UI |
| **Alignment** | ❌ Manual required | ✅ Interactive tool |
| **Learning curve** | ❌ Steeper | ✅ Easier |
| **Scripting** | ✅ Excellent | ❌ None |
| **HPC integration** | ✅ Best | ❌ Not suitable |

**Recommendation:** Use CLI for production runs and automation, GUI for exploration and interactive workflows.

## Migration from GUI to CLI

1. **Create config in GUI**: Use GUI to set up your pipeline
2. **Save configuration**: Export as `focus_config.json`
3. **Test in CLI**: Run with `focus --config focus_config.json`
4. **Automate**: Integrate into scripts and workflows

## Advanced Workflows

### Conditional Execution

```bash
# Only run if input data exists
if [ -d "/data/project/raw_data" ]; then
  focus --config /data/project/focus_config.json
fi
```

### Chaining Pipelines

```bash
# Preprocessing only
focus --config preprocessing_config.json

# Check success
if [ $? -eq 0 ]; then
  # Alignment only
  focus --config alignment_config.json
  
  if [ $? -eq 0 ]; then
    # Registration only
    focus --config registration_config.json
  fi
fi
```

### Parameter Sweeping

```bash
# Test different registration parameters
for k in 3 5 7 9; do
  # Create config with different k_neighbors
  jq ".modalities[0].registration_settings.k_neighbors = $k" \
    template_config.json > config_k${k}.json
  
  # Run FOCUS
  focus --config config_k${k}.json
  
  # Evaluate results
  python evaluate_results.py --config config_k${k}.json
  
  # Clean up
  rm config_k${k}.json
done
```

## Security Considerations

### Configuration Files

- **Sensitive data**: Configuration files may contain HuggingFace tokens
- **Permissions**: Set appropriate file permissions
- **Cleanup**: Remove tokens from shared configurations

```bash
# Set restrictive permissions
chmod 600 focus_config.json

# Remove token before sharing
jq 'del(.huggingface_token)' focus_config.json > focus_config_shared.json
```

### Container Security

- **User namespaces**: Use `--userns=keep-id` for Podman
- **Read-only mounts**: Mount data read-only when possible
- **Resource limits**: Set CPU/memory limits

```bash
# Secure container execution
podman run --userns=keep-id \
  --read-only \
  --cpus=4 \
  --memory=16G \
  -v /data/project:/data/project:ro \
  focus.sif --config /data/project/focus_config.json
```

## Performance Benchmarking

### Timing Execution

```bash
# Simple timing
time focus --config /path/to/config.json

# Detailed profiling
/usr/bin/time -v focus --config /path/to/config.json 2> time.log
```

### Resource Monitoring

```bash
# Monitor CPU, memory, I/O
vmstat 1 > vmstat.log &
FOCUS_PID=$!
iostat -x 1 > iostat.log &

focus --config /path/to/config.json

kill $FOCUS_PID
```

### GPU Monitoring

```bash
# NVIDIA GPU monitoring
nvidia-smi --query-compute-apps=gpu_name,gpu_uuid,used_memory --format=csv -l 1 > gpu.log &

focus --config /path/to/config.json
```

## Next Steps

Now that you're familiar with the CLI:

1. **Explore GUI**: Try the [GUI Usage Guide](gui_usage.md) for interactive workflows
2. **Learn Configuration**: Deep dive into [Configuration Reference](../configuration/config_fields.md)
3. **Understand Pipeline**: Read about [Pipeline Stages](../pipeline/preprocessing.md)
4. **Deploy**: Set up [Container Deployment](../deployment/containers.md)

## Support

For CLI-related issues:

1. **Check logs**: Review pipeline logs for errors
2. **Validate config**: Ensure JSON configuration is valid
3. **Test components**: Run individual stages separately
4. **Report issues**: Provide configuration file and error logs

## Additional Resources

- [Configuration Reference](../configuration/config_fields.md)
- [Pipeline Documentation](../pipeline/preprocessing.md)
- [Troubleshooting Guide](../troubleshooting.md)
- [FAQ](../faq.md)