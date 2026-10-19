// Jenkins pipeline mirroring .github/workflows/ci.yml, plus an Ansible deploy stage.
// Needs: Docker on the agent, a "bidalert-deploy-ssh" SSH credential, and
// deploy/ansible/inventory.ini provided on the agent (it is git-ignored).
pipeline {
    agent any

    options {
        timeout(time: 30, unit: 'MINUTES')
        disableConcurrentBuilds()
        buildDiscarder(logRotator(numToKeepStr: '20'))
    }

    parameters {
        booleanParam(name: 'DEPLOY', defaultValue: false, description: 'Run the Ansible deploy after a green build on main')
        string(name: 'VERSION', defaultValue: 'main', description: 'Git ref to deploy')
    }

    environment {
        PIP_DISABLE_PIP_VERSION_CHECK = '1'
        IMAGE = "bidalert:${env.BUILD_NUMBER}"
    }

    stages {
        stage('Lint and test') {
            agent {
                docker {
                    image 'python:3.12-slim'
                    reuseNode true
                }
            }
            steps {
                sh '''
                    python -m venv .venv
                    . .venv/bin/activate
                    pip install -q -r requirements-dev.txt
                    pip install -q --no-deps -e .
                    ruff check src tests
                    ruff format --check src tests
                    mypy
                    pytest -q --junitxml=reports/junit.xml
                '''
            }
            post {
                always {
                    junit allowEmptyResults: true, testResults: 'reports/junit.xml'
                }
            }
        }

        stage('Docker image') {
            steps {
                sh 'docker build -t "$IMAGE" .'
                sh 'docker run --rm "$IMAGE" bidalert --help'
            }
        }

        stage('Validate deploy config') {
            agent {
                docker {
                    image 'python:3.12-slim'
                    reuseNode true
                }
            }
            steps {
                sh '''
                    python -m venv .venv-ansible
                    . .venv-ansible/bin/activate
                    pip install -q ansible-core ansible-lint
                    cd deploy/ansible
                    ansible-playbook -i inventory.example.ini site.yml --syntax-check
                    ansible-lint site.yml
                '''
            }
        }

        stage('Deploy') {
            when {
                allOf {
                    branch 'main'
                    expression { return params.DEPLOY }
                }
            }
            steps {
                input message: "Deploy ${params.VERSION} to production?", ok: 'Deploy'
                sshagent(credentials: ['bidalert-deploy-ssh']) {
                    sh '''
                        . .venv-ansible/bin/activate
                        ansible-playbook -i deploy/ansible/inventory.ini deploy/ansible/site.yml \
                          -e bidalert_version="$VERSION"
                    '''
                }
            }
        }
    }

    post {
        always {
            sh 'docker image rm -f "$IMAGE" || true'
        }
    }
}
