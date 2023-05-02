#!/usr/bin/env python
# -*- coding: utf-8 -*-
#
# Original work Copyright (C) 2014 University of Dundee
#                                   & Open Microscopy Environment.
#                    All Rights Reserved.
# Modified work Copyright 2022 Torec Luik, Amsterdam UMC
# Use is subject to license terms supplied in LICENSE.txt
#
# This script is used to run the CellPose segmentation algorithm on a Slurm cluster, using data exported from an Omero server.
#
# This script requires the SlurmClient and Fabric Python modules to be installed, as well as access to a Slurm cluster running the CellPose Singularity image.

from __future__ import print_function
import omero
from omero.grid import JobParams
from omero.rtypes import rstring, unwrap
import omero.scripts as omscripts
from typing import Dict, List, Optional, Tuple
import re
from fabric import Connection, Result
from paramiko import SSHException
import configparser


_IMAGE_EXPORT_SCRIPT = "_SLURM_Image_transfer.py"
_DEFAULT_MODEL = "nuclei"
_VALUES_MODELS = [rstring(_DEFAULT_MODEL), rstring("cyto")]
_PARAM_MODEL = "Model"
_PARAM_NUCCHANNEL = "Nuclear Channel"
_PARAM_PROBTHRESH = "Cell probability threshold"
_PARAM_DIAMETER = "Cell diameter"
_DEFAULT_MAIL = "No"
_DEFAULT_TIME = "00:15:00"


class SlurmClient(Connection):
    """A client for connecting to and interacting with a Slurm cluster over SSH.

    This class extends the Fabric Connection class, adding methods and attributes specific to working with Slurm.

    SlurmClient accepts the same arguments as Connection. So below only mentions the added ones:

    Attributes:
        slurm_data_path (str): The path to the directory containing the data files for Slurm jobs.
        slurm_images_path (str): The path to the directory containing the Singularity images for Slurm jobs.
        slurm_model_paths (dict): A dictionary containing the paths to the Singularity images for specific Slurm job models.
        slurm_script_path (str): The path to the directory containing the Slurm job submission scripts. This is expected to be a Git repository.

    Example:
        # Create a SlurmClient object as contextmanager

        with SlurmClient.from_config() as client:

            # Run a command on the remote host

            result = client.run('sbatch myjob.sh')

            # Check whether the command succeeded

            if result.ok:
                print('Job submitted successfully!')

            # Print the output of the command

            print(result.stdout)

    """
    _DEFAULT_CONFIG_PATH_1 = "/etc/slurm-config.ini"
    _DEFAULT_CONFIG_PATH_2 = "~/slurm-config.ini"
    _DEFAULT_HOST = "slurm"
    _DEFAULT_INLINE_SSH_ENV = True
    _DEFAULT_SLURM_DATA_PATH = "my-scratch/data"
    _DEFAULT_SLURM_IMAGES_PATH = "my-scratch/singularity_images/workflows"
    _DEFAULT_SLURM_GIT_SCRIPT_PATH = "slurm-scripts"
    _OUT_SEP = "--split--"
    _VERSION_CMD = "ls -h {slurm_images_path}/{image_path} | grep -oP '(?<=-)v.+(?=.simg)'"
    _DATA_CMD = "ls -h {slurm_data_path} | grep -oP '.+(?=.zip)'"
    _ACCT_CMD = "sacct --starttime {start_time} -o JobId -n -X"
    _ZIP_CMD = "7z a -y {filename} -tzip {data_location}/data/out"
    _LOGFILE = "omero-{slurm_job_id}.log"

    def __init__(self,
                 host=_DEFAULT_HOST,
                 user=None,
                 port=None,
                 config=None,
                 gateway=None,
                 forward_agent=None,
                 connect_timeout=None,
                 connect_kwargs=None,
                 inline_ssh_env=_DEFAULT_INLINE_SSH_ENV,
                 slurm_data_path: str = _DEFAULT_SLURM_DATA_PATH,
                 slurm_images_path: str = _DEFAULT_SLURM_IMAGES_PATH,
                 slurm_model_paths: dict = None,
                 slurm_script_path: str = _DEFAULT_SLURM_GIT_SCRIPT_PATH
                 ):
        super(SlurmClient, self).__init__(host,
                                          user,
                                          port,
                                          config,
                                          gateway,
                                          forward_agent,
                                          connect_timeout,
                                          connect_kwargs,
                                          inline_ssh_env)
        self.slurm_data_path = slurm_data_path
        self.slurm_images_path = slurm_images_path
        self.slurm_model_paths = slurm_model_paths
        self.slurm_script_path = slurm_script_path
        # TODO: setup the script path by downloading from GIT? setup all the directories?

    @classmethod
    def from_config(cls, configfile: str = '') -> 'SlurmClient':
        """Creates a new SlurmClient object using the parameters read from a configuration file (.ini).

        Defaults paths to look for config files are:
            - /etc/slurm-config.ini
            - ~/slurm-config.ini

        Note that this is only for the SLURM specific values that we added.
        Most configuration values are set via configuration mechanisms from Fabric library,
        like SSH settings being loaded from SSH config, /etc/fabric.yml or environment variables.
        See Fabric's documentation for more info on configuration if needed.

        Args:
            configfile (str): The path to your configuration file. Optional.

        Returns:
            SlurmClient: A new SlurmClient object.
        """
        # Load the configuration file
        configs = configparser.ConfigParser(allow_no_value=True)
        # Loads from default locations and given location, missing files are ok
        configs.read([cls._DEFAULT_CONFIG_PATH_1,
                     cls._DEFAULT_CONFIG_PATH_2, configfile])
        # Read the required parameters from the configuration file, fallback to defaults
        host = configs.get("SSH", "host", fallback=cls._DEFAULT_HOST)
        inline_ssh_env = configs.getboolean(
            "SSH", "inline_ssh_env", fallback=cls._DEFAULT_INLINE_SSH_ENV)
        slurm_data_path = configs.get(
            "SLURM", "slurm_data_path", fallback=cls._DEFAULT_SLURM_DATA_PATH)
        slurm_images_path = configs.get(
            "SLURM", "slurm_images_path", fallback=cls._DEFAULT_SLURM_IMAGES_PATH)
        slurm_model_paths = dict(configs.items("MODELS"))
        slurm_script_path = configs.get(
            "SLURM", "slurm_script_path", fallback=cls._DEFAULT_SLURM_GIT_SCRIPT_PATH)
        # Create the SlurmClient object with the parameters read from the config file
        return cls(host=host,
                   inline_ssh_env=inline_ssh_env,
                   slurm_data_path=slurm_data_path,
                   slurm_images_path=slurm_images_path,
                   slurm_model_paths=slurm_model_paths,
                   slurm_script_path=slurm_script_path)

    def validate(self):
        """Validate the connection to the Slurm cluster by running a simple command.

        Returns:
            bool: True if the command is executed successfully, False otherwise.
        """
        return self.run('echo " "').ok

    def run_commands(self, cmdlist: List[str], env: Optional[Dict[str, str]] = None, sep: str = ' && ') -> Result:
        """
        Runs a list of shell commands consecutively, ensuring success of each before calling the next.

        The environment variables can be set using the `env` argument. These commands retain the same session (environment variables
        etc.), unlike running them separately.

        Args:
            cmdlist (List[str]): A list of shell commands to run on SLURM.
            env (Optional[Dict[str, str]]): A dictionary of environment variables to be set for the commands (default: None).
            sep (str): The separator used to concatenate the commands (default: ' && ').

        Returns:
            Result: The result of the last command in the list.
        """
        if env is None:
            env = {}
        cmd = sep.join(cmdlist)
        print(f"Running commands, with env {env} and sep {sep}: {cmd}")
        return self.run(cmd, env=env)

    def run_commands_split_out(self, cmdlist: List[str], env: Optional[Dict[str, str]] = None) -> List[str]:
        """Runs a list of shell commands consecutively and splits the output of each command.

        Each command in the list is executed with a separator in between that is unique and can be used to split
        the output of each command later. The separator used is specified by the `_OUT_SEP` attribute of the
        SlurmClient instance.

        Args:
            cmdlist (List[str]): A list of shell commands to run.
            env (Optional[Dict[str, str]]): A dictionary of environment variables to set when running the commands.

        Returns:
            List[str]: A list of strings, where each string corresponds to the output of a single command
                    in `cmdlist` split by the separator `_OUT_SEP`.
        Raises:
            SSHException: If any of the commands fail to execute successfully.
        """
        result = self.run_commands(cmdlist=cmdlist,
                                   env=env,
                                   sep=f" && echo {self._OUT_SEP} && ")
        if result.ok:
            response = result.stdout
            split_responses = response.split(self._OUT_SEP)
            return split_responses
        else:
            error = f"Result is not ok: {result}"
            print(error)
            raise SSHException(error)

    def list_old_jobs(self, env: Optional[Dict[str, str]] = None) -> List[str]:
        """Get list of finished jobs from SLURM.

        Args:
            env (Optional[Dict[str, str]]): Optional environment variables to set when running the command.
                Defaults to None.

        Returns:
            List: List of Job Ids
        """

        cmd = self.get_old_job_command()
        print("Retrieving list of finished jobs from Slurm")
        result = self.run_commands([cmd], env=env)
        job_list = result.stdout.strip().split('\n')
        job_list.reverse()
        return job_list

    def get_old_job_command(self, start_time: str = "2023-01-01") -> str:
        return self._ACCT_CMD.format(start_time=start_time)

    def transfer_data(self, local_path: str) -> Result:
        """Transfers a file or directory from the local machine to the remote Slurm cluster.

        Args:
            local_path (str): The local path to the file or directory to transfer.

        Returns:
            Result: The result of the file transfer operation.
        """
        print(
            f"Transfering file {local_path} to {self.slurm_data_path}")
        return self.put(local=local_path, remote=self.slurm_data_path)

    def unpack_data(self, zipfile: str, env: Optional[Dict[str, str]] = None) -> Result:
        """Unpacks a zipped file on the remote Slurm cluster.

        Args:
            zipfile (str): The name of the zipped file to be unpacked.
            env (Optional[Dict[str, str]]): Optional environment variables to set when running the command.
                Defaults to None.

        Returns:
            Result: The result of the command.

        """
        cmd = self.get_unzip_command(zipfile)
        print(f"Unpacking {zipfile} on Slurm")
        return self.run_commands([cmd], env=env)

    def update_slurm_scripts(self, env: Optional[Dict[str, str]] = None) -> Result:
        """Updates the local copy of the Slurm job submission scripts.

        This function pulls the latest version of the scripts from the Git repository,
        and copies them to the slurm_script_path directory.

        Args:
            env (Optional[Dict[str, str]]): Optional environment variables to set when running the command.
                Defaults to None.

        Returns:
            Result: The result of the command.
        """
        cmd = self.get_update_slurm_scripts_command()
        print("Updating Slurm job scripts on Slurm")
        return self.run_commands([cmd], env=env)

    def run_cellpose(self, cellpose_version, input_data, cp_model, nuc_channel, prob_threshold, cell_diameter, email, time) -> Result:
        """
        Runs CellPose on Slurm on the specified input data using the given parameters.

        Args:
            cellpose_version (str): The version of CellPose to use.
            input_data (str): The name of the input data folder containing the input image files.
            cp_model (str): The name of the CellPose model to use for segmentation.
            nuc_channel (int): The index of the nuclear channel in the image data.
            prob_threshold (float): The threshold probability value for object segmentation.
            cell_diameter (int): The approximate diameter of the cells in pixels.
            email (str): The email address to use for Slurm job notifications.
            time (str): The time limit for the Slurm job in the format HH:MM:SS.

        Returns:
            Result: An object containing the output from starting the CellPose job.

        """
        sbatch_cmd, sbatch_env = self.get_cellpose_command(
            cellpose_version, input_data, cp_model, nuc_channel, prob_threshold, cell_diameter, email, time)
        print("Running CellPose job on Slurm")
        return self.run_commands([sbatch_cmd], sbatch_env)

    def get_update_slurm_scripts_command(self) -> str:
        """Generates the command to update the Git repository containing the Slurm scripts, if necessary.

        Returns:
            str: A string containing the Git command to update the Slurm scripts.
        """
        update_cmd = f"git -C {self.slurm_script_path} pull"
        return update_cmd

    def check_job_status(self, slurm_job_id: str, env: Optional[Dict[str, str]] = None) -> Result:
        """
        Checks the status of a Slurm job with the given job ID.

        Args:
            slurm_job_id (str): The job ID of the Slurm job to check.
            env (Optional[Dict[str, str]]): A dictionary of environment variables to set before executing the command. Defaults to None.

        Returns:
            Result: The result of the command execution.
        """
        cmd = self.get_job_status_command(slurm_job_id)
        print(f"Getting status of {slurm_job_id} on Slurm")
        return self.run_commands([cmd], env=env)

    def get_job_status_command(self, slurm_job_id: str) -> str:
        """
        Returns the Slurm command to get the status of a job with the given job ID.

        Args:
            slurm_job_id (str): The job ID of the job to check.

        Returns:
            str: The Slurm command to get the status of the job.
        """

        return f"sacct -n -o JobId,State,End -X -j {slurm_job_id}"

    def get_cellpose_command(self, image_version, input_data, cp_model, nuc_channel, prob_threshold, cell_diameter, email=None, time=None, model="cellpose", job_script="cellpose.sh") -> Tuple[str, dict]:
        """
        Returns the command and environment dictionary to run a CellPose job on the Slurm workload manager.

        Args:
            image_version (str): The version of the Singularity image to use.
            input_data (str): The name of the input data folder on the shared file system.
            cp_model (str): The name of the CellPose model to use.
            nuc_channel (int): The index of the nuclear channel.
            prob_threshold (float): The probability threshold for nuclei detection.
            cell_diameter (float): The expected cell diameter in pixels.
            email (Optional[str]): The email address to send notifications to (default is None).
            time (Optional[str]): The maximum time for the job to run (default is None).
            model (str): The name of the folder of the Docker image to use (default is "cellpose").
            job_script (str): The name of the Slurm job script to use (default is "cellpose.sh").

        Returns:
            Tuple[str, dict]: A tuple containing the Slurm sbatch command and the environment dictionary.

        """
        sbatch_env = {
            "DATA_PATH": f"{self.slurm_data_path}/{input_data}",
            "IMAGE_PATH": f"{self.slurm_images_path}/{model}",
            "IMAGE_VERSION": f"{image_version}",
        }
        cellpose_env = {
            "DIAMETER": f"{cell_diameter}",
            "PROB_THRESHOLD": f"{prob_threshold}",
            "NUC_CHANNEL": f"{nuc_channel}",
            "CP_MODEL": f"{cp_model}",
            "USE_GPU": "true",
        }
        env = {**sbatch_env, **cellpose_env}

        email_param = "" if email is None else f" --mail-user={email}"
        time_param = "" if time is None else f" --time={time}"
        job_params = [time_param, email_param]
        job_param = "".join(job_params)
        sbatch_cmd = f"sbatch{job_param} --output=omero-%4j.log {self.slurm_script_path}/jobs/{job_script}"

        return sbatch_cmd, env

    def copy_zip_locally(self, local_tmp_storage: str, filename: str) -> Result:
        """ Copy zip from SLURM to local server

        Args:
            local_tmp_storage (String): Path to store zip
            filename (String): Zip filename on Slurm
        """

        results = self.get(
            remote=f"{filename}.zip",
            local=local_tmp_storage)
        print(f"Ran slurm: {results.stdout}")
        return results

    def zip_data_on_slurm_server(self, data_location: str, filename: str, env: Optional[Dict[str, str]] = None) -> Result:
        """Zip the output folder of a job on SLURM

        Args:
            data_location (String): Folder on SLURM with the "data/out" subfolder
            filename (String): Name to give to the zipfile
        """
        # zip
        zip_cmd = self.get_zip_command(data_location, filename)
        print(f"Zipping {data_location} as {filename} on Slurm")
        return self.run_commands([zip_cmd], env=env)

    def get_zip_command(self, data_location: str, filename: str) -> str:
        return self._ZIP_CMD.format(filename=filename, data_location=data_location)

    def get_logfile_from_slurm(self, slurm_job_id: str, local_tmp_storage: str = "/tmp/", logfile: str = None) -> Tuple[str, str, Result]:
        """Copy the logfile of given SLURM job to local server

        Args:
            slurm_job_id (String): ID of the SLURM job

        Returns:
            Tuple: directory, full path of the logfile, and run Result
        """
        if logfile is None:
            logfile = self._LOGFILE
        logfile = logfile.format(slurm_job_id=slurm_job_id)
        result = self.get(
            remote=logfile,
            local=local_tmp_storage)
        print(f"Ran slurm {result.stdout}")
        export_file = local_tmp_storage+logfile
        return local_tmp_storage, export_file, result

    def get_unzip_command(self, zipfile: str, filter_filetypes: str = "*.tiff *.tif") -> str:
        """
        Generate a command string for unzipping a data archive and creating 
        required directories for Slurm jobs.

        Args:
            zipfile (str): The name of the zip archive file to extract. Without extension.
            filter_filetypes (str, optional): A space-separated string containing the file extensions to extract
            from the zip file. The default value is "*.tiff *.tif".
            Setting this argument to `None` or '*' will omit the file filter and extract all files.

        Returns:
            str: The command to extract the specified filetypes from the zip file.

        """
        if filter_filetypes is None:
            filter_filetypes = '*'  # omit filter
        unzip_cmd = f"mkdir {self.slurm_data_path}/{zipfile} \
                    {self.slurm_data_path}/{zipfile}/data \
                    {self.slurm_data_path}/{zipfile}/data/in \
                    {self.slurm_data_path}/{zipfile}/data/out \
                    {self.slurm_data_path}/{zipfile}/data/gt; \
                    7z e -y -o{self.slurm_data_path}/{zipfile}/data/in \
                    {self.slurm_data_path}/{zipfile}.zip {filter_filetypes}"

        return unzip_cmd

    def get_image_versions_and_data_files(self, model: str) -> List[List[str]]:
        """
        Gets the available image versions and (input) data files for a given model.

        Args:
            model (str): The name of the model to query for.

        Returns:
            List[List[str]]: A list of 2 lists, the first containing the available image versions
            and the second containing the available data files.
        Raises:
            ValueError: If the provided model is not found in the SlurmClient's known model paths.
        """
        try:
            image_path = self.slurm_model_paths.get(model)
        except KeyError:
            raise ValueError(
                f"No path known for provided model {model}, in {self.slurm_model_paths}")
        cmdlist = [self._VERSION_CMD.format(slurm_images_path=self.slurm_images_path,
                                            image_path=image_path),
                   self._DATA_CMD.format(slurm_data_path=self.slurm_data_path)]
        # split responses per command
        response_list = self.run_commands_split_out(cmdlist)
        # split lines further into sublists
        response_list = [response.strip().split('\n')
                         for response in response_list]
        return response_list[0], response_list[1]


def runScript():
    """
    The main entry point of the script
    """

    with SlurmClient.from_config() as slurmClient:

        params = JobParams()
        params.authors = ["Torec Luik"]
        params.version = "0.0.3"
        params.description = f'''Script to run CellPose on slurm cluster.
        First run the {_IMAGE_EXPORT_SCRIPT} script to export your data to the cluster.
        
        Specifically will run: 
        https://hub.docker.com/r/torecluik/t_nucleisegmentation-cellpose
        

        This runs a script remotely on the Slurm cluster.
        Connection ready? {slurmClient.validate()}
        '''
        params.name = 'Slurm Cellpose Segmentation'
        params.contact = 't.t.luik@amsterdamumc.nl'
        params.institutions = ["Amsterdam UMC"]
        params.authorsInstitutions = [[1]]

        _versions, _datafiles = slurmClient.get_image_versions_and_data_files(
            'cellpose')
        input_list = [
            omscripts.Bool("CellPose", grouping="04", default=True),
            omscripts.String(_PARAM_MODEL, optional=False, grouping="04.3",
                             values=_VALUES_MODELS, default=_DEFAULT_MODEL),
            omscripts.Int(_PARAM_NUCCHANNEL, optional=True, grouping="04.4",
                          description="Channel with the nuclei (to segment)",
                          default=0),
            omscripts.Float(_PARAM_PROBTHRESH, optional=True, grouping="04.5",
                            description="threshold when to segment (0 = everything, 1 = nothing)",
                            default=0.5),
            omscripts.Float(_PARAM_DIAMETER, optional=True, grouping="04.6",
                            description="Diameter of a cell. Leave at 0 to let the computer guess.",
                            default=0),
            omscripts.String("Folder_Name", grouping="05",
                             description=f"Name of folder where images are stored, as provided with {_IMAGE_EXPORT_SCRIPT}",
                             values=_datafiles),
            omscripts.Bool("Slurm Job Parameters",
                           grouping="06", default=True),
            omscripts.String("Version", grouping="06.1",
                             description="Version of the Singularity Image of Cellpose",
                             values=_versions),
            omscripts.String("Duration", grouping="06.2",
                             description="Maximum time the script should run for. Max is 8 hours. Notation is hh:mm:ss",
                             default=_DEFAULT_TIME),
            omscripts.String("E-mail", grouping="06.3",
                             description="Provide an e-mail if you want a mail when your job is done or cancelled.",
                             default=_DEFAULT_MAIL)
        ]
        inputs = {
            p._name: p for p in input_list
        }
        params.inputs = inputs
        params.namespaces = [omero.constants.namespaces.NSDYNAMIC]
        client = omscripts.client(params)

        # Unpack script input values
        cellpose_version = unwrap(client.getInput("Version"))
        zipfile = unwrap(client.getInput("Folder_Name"))
        cp_model = unwrap(client.getInput(_PARAM_MODEL))
        nuc_channel = unwrap(client.getInput(_PARAM_NUCCHANNEL))
        prob_threshold = unwrap(client.getInput(_PARAM_PROBTHRESH))
        cell_diameter = unwrap(client.getInput(_PARAM_DIAMETER))
        email = unwrap(client.getInput("E-mail"))
        if email == _DEFAULT_MAIL:
            email = None
        time = unwrap(client.getInput("Duration"))

        try:
            # 3. Call SLURM (segmentation)
            unpack_result = slurmClient.unpack_data(zipfile)
            print(unpack_result.stdout)
            if not unpack_result.ok:
                print("Error unpacking data:", unpack_result.stderr)
            else:
                update_result = slurmClient.update_slurm_scripts()
                print(update_result.stdout)
                if not update_result.ok:
                    print("Error updating SLURM scripts:", update_result.stderr)
                else:
                    cp_result = slurmClient.run_cellpose(cellpose_version,
                                                         zipfile,
                                                         cp_model,
                                                         nuc_channel,
                                                         prob_threshold,
                                                         cell_diameter,
                                                         email,
                                                         time)
                    print(cp_result.stdout)
                    if not cp_result.ok:
                        print("Error running CellPose job:", cp_result.stderr)
                    else:
                        slurm_job_id = next((int(s.strip()) for s in cp_result.stdout.split(
                            "Submitted batch job") if s.strip().isdigit()), -1)
                        print_result = f"Submitted to Slurm as batch job {slurm_job_id}."
                        # 4. Poll SLURM results
                        try:
                            poll_result = slurmClient.check_job_status(
                                slurm_job_id)
                            print(poll_result.stdout)
                            if not poll_result.ok:
                                print("Error checking job status:",
                                      poll_result.stderr)
                            else:
                                print_result += f"\n{poll_result.stdout}"
                        except Exception as e:
                            print_result += f" ERROR WITH JOB: {e}"
                            print(print_result)

             # 7. Script output
            client.setOutput("Message", rstring(print_result))
        finally:
            client.closeSession()


if __name__ == '__main__':
    runScript()
