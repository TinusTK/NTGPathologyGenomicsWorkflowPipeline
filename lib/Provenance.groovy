import groovy.json.JsonOutput
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths

/**
 * Writes pipeline_info/provenance.json at the end of a run: pipeline commit,
 * Nextflow version, tool versions, container images and digests, and all
 * parameters, so every result can be traced back to exactly what made it.
 */
class Provenance {

    static void write(workflow, Map params, Path projectDir) {
        Path infoDir = Paths.get(params.outdir.toString(), 'pipeline_info').toAbsolutePath()
        Files.createDirectories(infoDir)
        Map versions = toolVersions(infoDir.resolve('tool_versions.yml'))

        def record = [
            pipeline  : [
                name       : workflow.manifest.name,
                version    : workflow.manifest.version,
                git_commit : git(projectDir, 'rev-parse', 'HEAD'),
                git_dirty  : (git(projectDir, 'status', '--porcelain') ?: '') != '',
                reproduces : 'Galaxy Australia: Genomic Assembly and analysis - RDH version 5.2',
            ],
            run       : [
                name        : workflow.runName,
                session_id  : workflow.sessionId.toString(),
                started     : workflow.start?.toString(),
                completed   : workflow.complete?.toString(),
                duration    : workflow.duration?.toString(),
                success     : workflow.success,
                exit_status : workflow.exitStatus,
                profile     : workflow.profile,
                command_line: workflow.commandLine,
            ],
            nextflow  : [version: workflow.nextflow.version.toString(), build: workflow.nextflow.build],
            workflow  : [id: params.workflow_id, version: params.workflow_version, name: params.workflow_name],
            tools     : versions.findAll { k, v -> !k.startsWith('container.') && !k.startsWith('note.') },
            // decisions made while running, e.g. "checkm.tree: reduced (auto; 17 GB RAM ...)"
            notes     : versions.findAll { k, v -> k.startsWith('note.') }.collectEntries { k, v -> [k.substring(5), v] },
            steps     : params.steps,
            databases : params.databases,
            containers: containers(workflow, versions),
            params    : params.collectEntries { k, v -> [k, v instanceof Map ? v : v?.toString()] },
        ]

        infoDir.resolve('provenance.json').text = JsonOutput.prettyPrint(JsonOutput.toJson(record)) + '\n'
    }

    // "tool: version" lines collected from every task's versions.yml
    private static Map toolVersions(Path file) {
        if (!Files.exists(file)) return [:]
        file.readLines().findAll { it.contains(':') }.collectEntries {
            def (k, v) = it.split(':', 2)*.trim()
            [k, v]
        }.sort()
    }

    // Image per process (each task writes "container.<PROCESS>: <image>" into
    // its versions.yml), plus the registry digest when the engine is Docker
    private static Map containers(workflow, Map versions) {
        versions.findAll { k, v -> k.startsWith('container.') }.collectEntries { k, image ->
            def digest = workflow.containerEngine == 'docker' ?
                run('docker', 'image', 'inspect', '--format', '{{index .RepoDigests 0}}', image) : null
            [k.substring('container.'.length()), [image: image, digest: digest]]
        }.sort()
    }

    private static String git(Path dir, String... args) {
        run(*(['git', '-C', dir.toString()] + args.toList()))
    }

    private static String run(String... cmd) {
        try {
            def p = new ProcessBuilder(cmd).redirectErrorStream(true).start()
            def out = p.inputStream.text.trim()
            return p.waitFor() == 0 ? out : null
        } catch (Exception ignored) {
            return null
        }
    }
}
