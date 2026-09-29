pipeline {
    agent any

    options {
        timestamps()
        disableConcurrentBuilds()
    }

    stages {

        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Python Syntax') {
            steps {
                sh '''
                    python3 -m py_compile automation/*.py web/*.py
                '''
            }
        }

        stage('Render Configs') {
            steps {
                sh '''
                    set -e

                    for device in R1 R2 R3 R4 R5 S1 S2 S3 S4; do
                        echo "=== Rendering $device ==="
                        python3 automation/render_config.py "$device"
                    done
                '''
            }
        }

        stage('Config Diff') {
            steps {
                sh '''
                    mkdir -p reports/config_changes
                    python3 automation/config_change.py --all
                '''
            }
        }
    }

    post {

        always {
            archiveArtifacts(
                artifacts: 'configs/generated/**/*.cfg,reports/config_changes/**/*.json',
                allowEmptyArchive: true
            )
        }

        success {
            echo 'NMAS CI pipeline passed.'
        }

        failure {
            echo 'NMAS CI pipeline failed.'
        }
    }
}
