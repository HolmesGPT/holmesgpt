import json
import logging
import os
import re
import shlex
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from datetime import datetime
from enum import Enum
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    OrderedDict,
    Tuple,
    Union,
)

from jinja2 import Template
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FilePath,
    PrivateAttr,
    model_validator,
)
from rich.console import Console
from rich.table import Table

from holmes.core.llm import LLM
from holmes.core.openai_formatting import format_tool_to_open_ai_standard
from holmes.core.transformers import (
    Transformer,
    TransformerError,
    registry,
)
from holmes.plugins.prompts import load_and_render_prompt
from holmes.utils.config_utils import merge_transformers
from holmes.utils.memory_limit import check_oom_and_append_hint, get_ulimit_prefix

if TYPE_CHECKING:
    from holmes.core.transformers import BaseTransformer

logger = logging.getLogger(__name__)


class StructuredToolResultStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"
    NO_DATA = "no_data"
    APPROVAL_REQUIRED = "approval_required"

    def to_color(self) -> str:
        if self == StructuredToolResultStatus.SUCCESS:
            return "green"
        elif self == StructuredToolResultStatus.ERROR:
            return "red"
        elif self == StructuredToolResultStatus.APPROVAL_REQUIRED:
            return "yellow"
        else:
            return "white"

    def to_emoji(self) -> str:
        if self == StructuredToolResultStatus.SUCCESS:
            return "✔"
        elif self == StructuredToolResultStatus.ERROR:
            return "❌"
        elif self == StructuredToolResultStatus.APPROVAL_REQUIRED:
            return "⚠️"
        else:
            return "⚪️"


class StructuredToolResult(BaseModel):
    schema_version: str = "robusta:v1.0.0"
    status: StructuredToolResultStatus
    error: Optional[str] = None
    return_code: Optional[int] = None
    data: Optional[Any] = None
    url: Optional[str] = None
    invocation: Optional[str] = None
    params: Optional[Dict] = None
    icon_url: Optional[str] = None

    def get_stringified_data(self) -> str:
        if self.data is None:
            return ""

        if isinstance(self.data, str):
            return self.data
        else:
            try:
                if isinstance(self.data, BaseModel):
                    return self.data.model_dump_json()
                else:
                    return json.dumps(
                        self.data, separators=(",", ":"), ensure_ascii=False
                    )
            except Exception:
                return str(self.data)


def sanitize(param):
    # allow empty strings to be unquoted - useful for optional params
    # it is up to the user to ensure that the command they are using is ok with empty strings
    # and if not to take that into account via an appropriate jinja template
    if param == "":
        return ""

    return shlex.quote(str(param))


def sanitize_params(params):
    return {k: sanitize(str(v)) for k, v in params.items()}


class ToolsetStatusEnum(str, Enum):
    ENABLED = "enabled"
    DISABLED = "disabled"
    FAILED = "failed"


class ToolsetTag(str, Enum):
    CORE = "core"
    CLUSTER = "cluster"
    CLI = "cli"


class ToolsetType(str, Enum):
    BUILTIN = "built-in"
    CUSTOMIZED = "custom"
    MCP = "mcp"


class ToolParameter(BaseModel):
    description: Optional[str] = None
    type: str = "string"
    required: bool = True
    properties: Optional[Dict[str, "ToolParameter"]] = None  # For object types
    items: Optional["ToolParameter"] = None  # For array item schemas
    enum: Optional[List[str]] = None  # For restricting to specific values


class ToolInvokeContext(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    tool_number: Optional[int] = None
    user_approved: bool = False
    llm: LLM
    max_token_count: int
    tool_call_id: str
    tool_name: str


class Tool(ABC, BaseModel):
    name: str
    description: str
    parameters: Dict[str, ToolParameter] = {}
    user_description: Optional[str] = (
        None  # templated string to show to the user describing this tool invocation (not seen by llm)
    )
    additional_instructions: Optional[str] = None
    icon_url: Optional[str] = Field(
        default=None,
        description="The URL of the icon for the tool, if None will get toolset icon",
    )
    transformers: Optional[List[Transformer]] = None

    # Private attribute to store initialized transformer instances for performance
    _transformer_instances: Optional[List["BaseTransformer"]] = PrivateAttr(
        default=None
    )

    def model_post_init(self, __context) -> None:
        """Initialize transformer instances once during tool creation for better performance."""
        logger.debug(
            f"Tool '{self.name}' model_post_init: creating transformer instances"
        )

        if self.transformers:
            logger.debug(
                f"Tool '{self.name}' has {len(self.transformers)} transformers to initialize"
            )
            self._transformer_instances = []
            for transformer in self.transformers:
                if not transformer:
                    continue
                logger.debug(
                    f"  Initializing transformer '{transformer.name}' with config: {transformer.config}"
                )
                try:
                    # Create transformer instance once and cache it
                    transformer_instance = registry.create_transformer(
                        transformer.name, transformer.config
                    )
                    self._transformer_instances.append(transformer_instance)
                    logger.debug(
                        f"Initialized transformer '{transformer.name}' for tool '{self.name}'"
                    )
                except Exception as e:
                    logger.warning(
                        f"Failed to initialize transformer '{transformer.name}' for tool '{self.name}': {e}"
                    )
                    # Continue with other transformers, don't fail the entire initialization
                    continue
        else:
            logger.debug(f"Tool '{self.name}' has no transformers")
            self._transformer_instances = None

    def get_openai_format(self, target_model: str):
        return format_tool_to_open_ai_standard(
            tool_name=self.name,
            tool_description=self.description,
            tool_parameters=self.parameters,
            target_model=target_model,
        )

    def invoke(
        self,
        params: Dict,
        context: ToolInvokeContext,
    ) -> StructuredToolResult:
        tool_number_str = f"#{context.tool_number} " if context.tool_number else ""
        logger.info(
            f"Running tool {tool_number_str}[bold]{self.name}[/bold]: {self.get_parameterized_one_liner(params)}"
        )
        start_time = time.time()
        result = self._invoke(params=params, context=context)
        result.icon_url = self.icon_url

        # Apply transformers to the result
        transformed_result = self._apply_transformers(result)
        elapsed = time.time() - start_time
        output_str = (
            transformed_result.get_stringified_data()
            if hasattr(transformed_result, "get_stringified_data")
            else str(transformed_result)
        )
        show_hint = f"/show {context.tool_number}" if context.tool_number else "/show"
        line_count = output_str.count("\n") + 1 if output_str else 0
        logger.info(
            f"  [dim]Finished {tool_number_str}in {elapsed:.2f}s, output length: {len(output_str):,} characters ({line_count:,} lines) - {show_hint} to view contents[/dim]"
        )
        return transformed_result

    def _apply_transformers(self, result: StructuredToolResult) -> StructuredToolResult:
        """
        Apply configured transformers to the tool result.

        Args:
            result: The original tool result

        Returns:
            The tool result with transformed data, or original result if transformation fails
        """
        if (
            not self._transformer_instances
            or result.status != StructuredToolResultStatus.SUCCESS
        ):
            return result

        # Get the output string to transform
        original_data = result.get_stringified_data()
        if not original_data:
            return result

        transformed_data = original_data
        transformers_applied = []

        # Use cached transformer instances instead of creating new ones
        for transformer_instance in self._transformer_instances:
            try:
                # Check if transformer should be applied
                if not transformer_instance.should_apply(transformed_data):
                    logger.debug(
                        f"Transformer '{transformer_instance.name}' skipped for tool '{self.name}' (conditions not met)"
                    )
                    continue

                # Apply transformation
                pre_transform_size = len(transformed_data)
                transform_start_time = time.time()
                original_data = transformed_data  # Keep a copy for potential reversion
                transformed_data = transformer_instance.transform(transformed_data)
                transform_elapsed = time.time() - transform_start_time

                # Check if this is llm_summarize and revert if summary is not smaller
                post_transform_size = len(transformed_data)
                if (
                    transformer_instance.name == "llm_summarize"
                    and post_transform_size >= pre_transform_size
                ):
                    # Revert to original data if summary is not smaller
                    transformed_data = original_data
                    logger.debug(
                        f"Transformer '{transformer_instance.name}' reverted for tool '{self.name}' "
                        f"(output size {post_transform_size:,} >= input size {pre_transform_size:,})"
                    )
                    continue  # Don't mark as applied

                transformers_applied.append(transformer_instance.name)

                # Generic logging - transformers can override this with their own specific metrics
                size_change = post_transform_size - pre_transform_size
                logger.info(
                    f"Applied transformer '{transformer_instance.name}' to tool '{self.name}' output "
                    f"in {transform_elapsed:.2f}s (size: {pre_transform_size:,} → {post_transform_size:,} chars, "
                    f"change: {size_change:+,})"
                )

            except TransformerError as e:
                logger.warning(
                    f"Transformer '{transformer_instance.name}' failed for tool '{self.name}': {e}"
                )
                # Continue with other transformers, don't fail the entire chain
                continue
            except Exception as e:
                logger.error(
                    f"Unexpected error applying transformer '{transformer_instance.name}' to tool '{self.name}': {e}"
                )
                # Continue with other transformers
                continue

        # If any transformers were applied, update the result
        if transformers_applied:
            # Create a copy of the result with transformed data
            result_dict = result.model_dump(exclude={"data"})
            result_dict["data"] = transformed_data
            return StructuredToolResult(**result_dict)

        return result

    @abstractmethod
    def _invoke(
        self,
        params: dict,
        context: ToolInvokeContext,
    ) -> StructuredToolResult:
        """
        params: the tool params
        user_approved: whether the tool call is approved by the user. Can be used to confidently execute unsafe actions.
        """
        pass

    @abstractmethod
    def get_parameterized_one_liner(self, params: Dict) -> str:
        return ""


class YAMLTool(Tool, BaseModel):
    command: Optional[str] = None
    script: Optional[str] = None

    def __init__(self, **data):
        super().__init__(**data)
        self.__infer_parameters()

    def __infer_parameters(self):
        # Find parameters that appear inside self.command or self.script but weren't declared in parameters
        template = self.command or self.script
        inferred_params = re.findall(r"\{\{\s*([\w]+)[\.\|]?.*?\s*\}\}", template)
        # TODO: if filters were used in template, take only the variable name
        # Regular expression to match Jinja2 placeholders with or without filters
        # inferred_params = re.findall(r'\{\{\s*(\w+)(\s*\|\s*[^}]+)?\s*\}\}', self.command)
        # for param_tuple in inferred_params:
        #    param = param_tuple[0]  # Extract the parameter name
        #    if param not in self.parameters:
        #        self.parameters[param] = ToolParameter()
        for param in inferred_params:
            if param not in self.parameters:
                self.parameters[param] = ToolParameter()

    def get_parameterized_one_liner(self, params) -> str:
        params = sanitize_params(params)
        if self.user_description:
            template = Template(self.user_description)
        else:
            cmd_or_script = self.command or self.script
            template = Template(cmd_or_script)  # type: ignore
        return template.render(params)

    def _build_context(self, params):
        params = sanitize_params(params)
        context = {**params}
        return context

    def _get_status(
        self, return_code: int, raw_output: str
    ) -> StructuredToolResultStatus:
        if return_code != 0:
            return StructuredToolResultStatus.ERROR
        if raw_output == "":
            return StructuredToolResultStatus.NO_DATA
        return StructuredToolResultStatus.SUCCESS

    def _invoke(
        self,
        params: dict,
        context: ToolInvokeContext,
    ) -> StructuredToolResult:
        if self.command is not None:
            raw_output, return_code, invocation = self.__invoke_command(params)
        else:
            raw_output, return_code, invocation = self.__invoke_script(params)  # type: ignore

        if self.additional_instructions and return_code == 0:
            logger.info(
                f"Applying additional instructions: {self.additional_instructions}"
            )
            output_with_instructions = self.__apply_additional_instructions(raw_output)
        else:
            output_with_instructions = raw_output

        error = (
            None
            if return_code == 0
            else f"Command `{invocation}` failed with return code {return_code}\nOutput:\n{raw_output}"
        )
        status = self._get_status(return_code, raw_output)

        return StructuredToolResult(
            status=status,
            error=error,
            return_code=return_code,
            data=output_with_instructions,
            params=params,
            invocation=invocation,
        )

    def __apply_additional_instructions(self, raw_output: str) -> str:
        try:
            result = subprocess.run(
                self.additional_instructions,  # type: ignore
                input=raw_output,
                shell=True,
                text=True,
                capture_output=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logger.error(
                f"Failed to apply additional instructions: {self.additional_instructions}. "
                f"Error: {e.stderr}"
            )
            return f"Error applying additional instructions: {e.stderr}"

    def __invoke_command(self, params) -> Tuple[str, int, str]:
        context = self._build_context(params)
        command = os.path.expandvars(self.command)  # type: ignore
        template = Template(command)  # type: ignore
        rendered_command = template.render(context)
        output, return_code = self.__execute_subprocess(rendered_command)
        return output, return_code, rendered_command

    def __invoke_script(self, params) -> str:
        context = self._build_context(params)
        script = os.path.expandvars(self.script)  # type: ignore
        template = Template(script)  # type: ignore
        rendered_script = template.render(context)

        with tempfile.NamedTemporaryFile(
            mode="w+", delete=False, suffix=".sh"
        ) as temp_script:
            temp_script.write(rendered_script)
            temp_script_path = temp_script.name
        subprocess.run(["chmod", "+x", temp_script_path], check=True)

        try:
            output, return_code = self.__execute_subprocess(temp_script_path)
        finally:
            subprocess.run(["rm", temp_script_path])
        return output, return_code, rendered_script  # type: ignore

    def __execute_subprocess(self, cmd) -> Tuple[str, int]:
        try:
            logger.debug(f"Running `{cmd}`")
            protected_cmd = get_ulimit_prefix() + cmd
            result = subprocess.run(
                protected_cmd,
                shell=True,
                text=True,
                check=False,  # do not throw error, we just return the error code
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )

            output = result.stdout.strip()
            output = check_oom_and_append_hint(output, result.returncode)
            return output, result.returncode
        except Exception as e:
            logger.error(
                f"An unexpected error occurred while running '{cmd}': {e}",
                exc_info=True,
            )
            output = f"Command execution failed with error: {e}"
            return output, 1


class StaticPrerequisite(BaseModel):
    enabled: bool
    disabled_reason: str


class CallablePrerequisite(BaseModel):
    callable: Callable[[dict[str, Any]], Tuple[bool, str]]


class ToolsetCommandPrerequisite(BaseModel):
    command: str  # must complete successfully (error code 0) for prereq to be satisfied
    expected_output: Optional[str] = None  # optional


class ToolsetEnvironmentPrerequisite(BaseModel):
    env: List[str] = []  # optional


class Toolset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    experimental: bool = False

    enabled: bool = False
    name: str
    description: str
    docs_url: Optional[str] = None
    icon_url: Optional[str] = None
    installation_instructions: Optional[str] = None
    additional_instructions: Optional[str] = ""
    prerequisites: List[
        Union[
            StaticPrerequisite,
            ToolsetCommandPrerequisite,
            ToolsetEnvironmentPrerequisite,
            CallablePrerequisite,
        ]
    ] = []
    tools: List[Tool]
    tags: List[ToolsetTag] = Field(
        default_factory=lambda: [ToolsetTag.CORE],
    )
    config: Optional[Any] = None
    is_default: bool = False
    llm_instructions: Optional[str] = None
    transformers: Optional[List[Transformer]] = None

    # warning! private attributes are not copied, which can lead to subtle bugs.
    # e.g. l.extend([some_tool]) will reset these private attribute to None

    # status fields that be cached
    type: Optional[ToolsetType] = None
    path: Optional[FilePath] = None
    status: ToolsetStatusEnum = ToolsetStatusEnum.DISABLED
    error: Optional[str] = None

    def override_with(self, override: "Toolset") -> None:
        """
        Overrides the current attributes with values from the Toolset loaded from custom config
        if they are not None.
        """
        for field, value in override.model_dump(
            exclude_unset=True,
            exclude=("name"),  # type: ignore
        ).items():
            if field in self.__class__.model_fields and value not in (None, [], {}, ""):
                setattr(self, field, value)

    @model_validator(mode="before")
    def preprocess_tools(cls, values):
        additional_instructions = values.get("additional_instructions", "")
        transformers = values.get("transformers", None)
        tools_data = values.get("tools", [])

        # Convert raw dict transformers to Transformer objects BEFORE merging
        if transformers:
            converted_transformers = []
            for t in transformers:
                if isinstance(t, dict):
                    try:
                        transformer_obj = Transformer(**t)
                        # Check if transformer is registered
                        from holmes.core.transformers import registry

                        if not registry.is_registered(transformer_obj.name):
                            logger.warning(
                                f"Invalid toolset transformer configuration: Transformer '{transformer_obj.name}' is not registered"
                            )
                            continue  # Skip invalid transformer
                        converted_transformers.append(transformer_obj)
                    except Exception as e:
                        # Log warning and skip invalid transformer
                        logger.warning(
                            f"Invalid toolset transformer configuration: {e}"
                        )
                        continue
                else:
                    # Already a Transformer object
                    converted_transformers.append(t)
            transformers = converted_transformers if converted_transformers else None

        tools = []
        for tool in tools_data:
            if isinstance(tool, dict):
                tool["additional_instructions"] = additional_instructions

                # Convert tool-level transformers to Transformer objects
                tool_transformers = tool.get("transformers")
                if tool_transformers:
                    converted_tool_transformers = []
                    for t in tool_transformers:
                        if isinstance(t, dict):
                            try:
                                transformer_obj = Transformer(**t)
                                # Check if transformer is registered
                                from holmes.core.transformers import registry

                                if not registry.is_registered(transformer_obj.name):
                                    logger.warning(
                                        f"Invalid tool transformer configuration: Transformer '{transformer_obj.name}' is not registered"
                                    )
                                    continue  # Skip invalid transformer
                                converted_tool_transformers.append(transformer_obj)
                            except Exception as e:
                                # Log warning and skip invalid transformer
                                logger.warning(
                                    f"Invalid tool transformer configuration: {e}"
                                )
                                continue
                        else:
                            # Already a Transformer object
                            converted_tool_transformers.append(t)
                    tool_transformers = (
                        converted_tool_transformers
                        if converted_tool_transformers
                        else None
                    )

                # Merge toolset-level transformers with tool-level configs
                tool["transformers"] = merge_transformers(
                    base_transformers=transformers,
                    override_transformers=tool_transformers,
                )
            if isinstance(tool, Tool):
                tool.additional_instructions = additional_instructions
                # Merge toolset-level transformers with tool-level configs
                tool.transformers = merge_transformers(  # type: ignore
                    base_transformers=transformers,
                    override_transformers=tool.transformers,
                )
            tools.append(tool)
        values["tools"] = tools

        return values

    def get_environment_variables(self) -> List[str]:
        env_vars = set()

        for prereq in self.prerequisites:
            if isinstance(prereq, ToolsetEnvironmentPrerequisite):
                env_vars.update(prereq.env)
        return list(env_vars)

    def interpolate_command(self, command: str) -> str:
        interpolated_command = os.path.expandvars(command)

        return interpolated_command

    def check_prerequisites(self):
        self.status = ToolsetStatusEnum.ENABLED

        # Sort prerequisites by type to fail fast on missing env vars before
        # running slow commands (e.g., ArgoCD checks that timeout):
        # 1. Static checks (instant)
        # 2. Environment variable checks (instant, often required by commands)
        # 3. Callable checks (variable speed)
        # 4. Command checks (slowest - may timeout or hang)
        def prereq_priority(prereq):
            if isinstance(prereq, StaticPrerequisite):
                return 0
            elif isinstance(prereq, ToolsetEnvironmentPrerequisite):
                return 1
            elif isinstance(prereq, CallablePrerequisite):
                return 2
            elif isinstance(prereq, ToolsetCommandPrerequisite):
                return 3
            return 4  # Unknown types go last

        sorted_prereqs = sorted(self.prerequisites, key=prereq_priority)

        for prereq in sorted_prereqs:
            if isinstance(prereq, ToolsetCommandPrerequisite):
                try:
                    command = self.interpolate_command(prereq.command)
                    result = subprocess.run(
                        command,
                        shell=True,
                        check=True,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    if (
                        prereq.expected_output
                        and prereq.expected_output not in result.stdout
                    ):
                        self.status = ToolsetStatusEnum.FAILED
                        self.error = f"`{prereq.command}` did not include `{prereq.expected_output}`"
                except subprocess.CalledProcessError as e:
                    self.status = ToolsetStatusEnum.FAILED
                    self.error = f"`{prereq.command}` returned {e.returncode}"

            elif isinstance(prereq, ToolsetEnvironmentPrerequisite):
                for env_var in prereq.env:
                    if env_var not in os.environ:
                        self.status = ToolsetStatusEnum.FAILED
                        self.error = f"Environment variable {env_var} was not set"

            elif isinstance(prereq, StaticPrerequisite):
                if not prereq.enabled:
                    self.status = ToolsetStatusEnum.FAILED
                    self.error = f"{prereq.disabled_reason}"

            elif isinstance(prereq, CallablePrerequisite):
                try:
                    (enabled, error_message) = prereq.callable(self.config)
                    if not enabled:
                        self.status = ToolsetStatusEnum.FAILED
                    if error_message:
                        self.error = f"{error_message}"
                except Exception as e:
                    self.status = ToolsetStatusEnum.FAILED
                    self.error = f"Prerequisite call failed unexpectedly: {str(e)}"

            if (
                self.status == ToolsetStatusEnum.DISABLED
                or self.status == ToolsetStatusEnum.FAILED
            ):
                logger.info(f"❌ Toolset {self.name}: {self.error}")
                # no point checking further prerequisites if one failed
                return

        logger.info(f"✅ Toolset {self.name}")

    @abstractmethod
    def get_example_config(self) -> Dict[str, Any]:
        return {}

    def _load_llm_instructions(self, jinja_template: str):
        tool_names = [t.name for t in self.tools]
        self.llm_instructions = load_and_render_prompt(
            prompt=jinja_template,
            context={"tool_names": tool_names, "config": self.config},
        )

    def _load_llm_instructions_from_file(self, file_dir: str, filename: str) -> None:
        """Helper method to load LLM instructions from a jinja2 template file.

        Args:
            file_dir: Directory where the template file is located (typically os.path.dirname(__file__))
            filename: Name of the jinja2 template file (e.g., "toolset_grafana_dashboard.jinja2")
        """
        template_file_path = os.path.abspath(os.path.join(file_dir, filename))
        self._load_llm_instructions(jinja_template=f"file://{template_file_path}")


class YAMLToolset(Toolset):
    tools: List[YAMLTool]  # type: ignore

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        if self.llm_instructions:
            self._load_llm_instructions(self.llm_instructions)

    def get_example_config(self) -> Dict[str, Any]:
        return {}


class ToolsetYamlFromConfig(Toolset):
    """
    ToolsetYamlFromConfig represents a toolset loaded from a YAML configuration file.
    To override a build-in toolset fields, we don't have to explicitly set all required fields,
    instead, we only put the fields we want to override in the YAML file.
    ToolsetYamlFromConfig helps py-pass the pydantic validation of the required fields and together with
    `override_with` method, a build-in toolset object with new configurations is created.
    """

    name: str
    # YamlToolset is loaded from a YAML file specified by the user and should be enabled by default
    # Built-in toolsets are exception and should be disabled by default when loaded
    enabled: bool = True
    additional_instructions: Optional[str] = None
    prerequisites: List[
        Union[
            StaticPrerequisite,
            ToolsetCommandPrerequisite,
            ToolsetEnvironmentPrerequisite,
        ]
    ] = []  # type: ignore
    tools: Optional[List[YAMLTool]] = []  # type: ignore
    description: Optional[str] = None  # type: ignore
    docs_url: Optional[str] = None
    icon_url: Optional[str] = None
    installation_instructions: Optional[str] = None
    config: Optional[Any] = None
    url: Optional[str] = None  # MCP toolset

    def get_example_config(self) -> Dict[str, Any]:
        return {}


class ToolsetDBModel(BaseModel):
    account_id: str
    cluster_id: str
    toolset_name: str
    icon_url: Optional[str] = None
    status: Optional[str] = None
    error: Optional[str] = None
    description: Optional[str] = None
    docs_url: Optional[str] = None
    installation_instructions: Optional[str] = None
    updated_at: str = Field(default_factory=datetime.now().isoformat)


def pretty_print_toolset_status(toolsets: list[Toolset], console: Console) -> None:
    status_fields = ["name", "enabled", "status", "type", "path", "error"]
    toolsets_status = []
    for toolset in sorted(toolsets, key=lambda ts: ts.status.value):
        toolset_status = json.loads(toolset.model_dump_json(include=status_fields))  # type: ignore

        status_value = toolset_status.get("status", "")
        error_value = toolset_status.get("error", "")
        if status_value == "enabled":
            toolset_status["status"] = "[green]enabled[/green]"
        elif status_value == "failed":
            toolset_status["status"] = "[red]failed[/red]"
            toolset_status["error"] = f"[red]{error_value}[/red]"
        else:
            toolset_status["status"] = f"[yellow]{status_value}[/yellow]"

        # Replace None with "" for Path and Error columns
        for field in ["path", "error"]:
            if toolset_status.get(field) is None:
                toolset_status[field] = ""

        order_toolset_status = OrderedDict(
            (k.capitalize(), toolset_status[k])
            for k in status_fields
            if k in toolset_status
        )
        toolsets_status.append(order_toolset_status)

    table = Table(show_header=True, header_style="bold")
    for col in status_fields:
        table.add_column(col.capitalize())

    for row in toolsets_status:
        table.add_row(*(str(row.get(col.capitalize(), "")) for col in status_fields))

    console.print(table)
